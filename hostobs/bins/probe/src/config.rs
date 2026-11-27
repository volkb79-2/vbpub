//! The probe's strict configuration and start-up argument rules.
//!
//! TOML supplies syntax and byte spans; semantic validation below supplies the probe's
//! requirements from `docs/PROBE-PROCESS.md` section (g). Unknown fields are refused at
//! every level, so a valid TOML spelling cannot silently add a setting.
//!
//! Protocol-wide keys have exactly one spelling: `[limits] max_rows`. A per-level form
//! such as `[limits.observe]` is an unknown section, so it cannot silently configure a
//! level while the probe-wide value keeps its default (P-06 backlog B3).

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::fs::File;
use std::io::Read;
use std::os::unix::net::UnixListener;
use std::path::{Component, Path, PathBuf};

use hostobs_serve::limits::Level;
use hostobs_serve::server::ServerLimits;
use hostobs_store::error::StoreError;
use hostobs_store::events::MAX_RING_BYTES;
use hostobs_store::store::{StoreConfig, StoreDirs};
use hostobs_store::tier::MAX_CAP_BYTES;
use serde::Deserialize;
use toml::Spanned;

/// Largest accepted configuration file.
pub(crate) const MAX_CONFIG_BYTES: usize = 64 * 1024;

const KEY_LEVELS: &str = "probe.levels";
const KEY_TICK_MS: &str = "probe.tick_ms";
const KEY_CGROUP_ROOT: &str = "probe.cgroup_root";
const KEY_MAX_ROWS: &str = "limits.max_rows";
const KEY_ANNOTATE_RATE: &str = "limits.annotate_rate_per_s";
const KEY_ANNOTATE_BURST: &str = "limits.annotate_burst";
const KEY_STORE_DIR: &str = "store.dir";
const KEY_STORE_TMPFS_DIR: &str = "store.tmpfs_dir";
const KEY_EVENTS_CAP_BYTES: &str = "store.events_cap_bytes";
const KEY_EVENTS_RING_BYTES: &str = "store.events_ring_bytes";
const EVENT_MAX_PAYLOAD: u32 = 16 * 1024;

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
    /// Sustained annotation rate per uid.
    pub(crate) annotate_rate_per_s: u32,
    /// Annotation burst capacity per uid.
    pub(crate) annotate_burst: u32,
    /// The explicit durable and tmpfs store paths and event bounds.
    pub(crate) store: StoreSettings,
}

/// Required store settings from `[store]`.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct StoreSettings {
    /// The durable root for cold and events tiers.
    pub(crate) dir: PathBuf,
    /// The independent tmpfs directory for hot and warm tiers.
    pub(crate) tmpfs_dir: PathBuf,
    /// The events tier's byte cap.
    pub(crate) events_cap_bytes: u64,
    /// The in-memory events ring's byte cap.
    pub(crate) events_ring_bytes: u64,
}

impl Config {
    /// The serving bounds with the two configured probe-wide keys applied.
    pub(crate) fn server_limits(&self) -> ServerLimits {
        let mut limits = ServerLimits::new();
        limits.probe.tick_ms = self.tick_ms;
        limits.probe.max_rows = self.max_rows;
        limits
    }

    /// Build the named plan-v4 store for this process epoch and explicit configured paths.
    pub(crate) fn store_config(&self, epoch: u64) -> Result<StoreConfig, StoreError> {
        let mut store = StoreConfig::plan_v4(&self.store.dir, epoch, 0)?;
        store.dirs = StoreDirs {
            tmpfs: Some(self.store.tmpfs_dir.clone()),
            cold: self.store.dir.join("cold"),
            events: self.store.dir.join("events"),
        };
        store.events.cap_bytes = self.store.events_cap_bytes;
        store.events = store.events.with_max_payload(EVENT_MAX_PAYLOAD)?;
        store.events_ring_bytes = self.store.events_ring_bytes;
        Ok(store)
    }
}

/// One command-line action, before phase-B wiring.
#[derive(Clone, Debug, Eq, PartialEq)]
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
pub(crate) fn parse_args(args: &[String]) -> Result<Action, ArgumentError> {
    match args {
        [version] if version == "--version" => Ok(Action::Version),
        [config, path] if config == "--config" && valid_path(path) => Ok(Action::Run {
            config: PathBuf::from(path),
        }),
        [config] if config == "--config" => {
            Err(ArgumentError(String::from("--config requires a path")))
        }
        [config, _] if config == "--config" => {
            Err(ArgumentError(String::from("--config requires a path")))
        }
        _ if args
            .iter()
            .filter(|argument| argument.as_str() == "--config")
            .count()
            > 1 =>
        {
            Err(ArgumentError(String::from(
                "--config may be given exactly once",
            )))
        }
        [] => Err(ArgumentError(String::from(
            "usage: hostobs-probe --config <path> | --version",
        ))),
        [unknown] => Err(ArgumentError(format!(
            "unexpected argument {unknown:?}; expected --config <path> or --version"
        ))),
        [first, second] => {
            let unknown = if first == "--version" { second } else { first };
            Err(ArgumentError(format!(
                "unexpected argument {unknown:?}; expected --config <path> or --version"
            )))
        }
        _ => Err(ArgumentError(String::from(
            "unexpected arguments; expected --config <path> or --version",
        ))),
    }
}

fn valid_path(path: &str) -> bool {
    !path.is_empty() && !path.starts_with("--")
}

/// A one-line configuration refusal naming its line and key.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ConfigError {
    line: usize,
    column: usize,
    key: String,
    message: String,
}

impl ConfigError {
    fn at(
        line: usize,
        column: usize,
        key: impl Into<String>,
        message: impl Into<String>,
    ) -> ConfigError {
        ConfigError {
            line,
            column,
            key: key.into(),
            message: message.into(),
        }
    }

    fn file(message: impl Into<String>) -> ConfigError {
        ConfigError::at(0, 0, "<file>", message)
    }

    fn semantic(
        source: &str,
        span: &std::ops::Range<usize>,
        key: &str,
        message: impl Into<String>,
    ) -> ConfigError {
        let (line, column) = position(source, span.start);
        ConfigError::at(line, column, key, message)
    }

    fn toml(source: &str, error: toml::de::Error) -> ConfigError {
        let message = one_line(error.message());
        let (line, column) = error
            .span()
            .as_ref()
            .map_or((0, 0), |span| position(source, span.start));
        let named =
            if message.starts_with("missing field `") || message.starts_with("unknown field `") {
                named_field(&message)
            } else {
                None
            };
        let key = named
            .or_else(|| {
                error
                    .span()
                    .map(|span| key_at(source, span.start))
                    .filter(|key| key != "<toml>")
            })
            .unwrap_or_else(|| String::from("<toml>"));
        ConfigError::at(line, column, key, message)
    }
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "line {}:{} key {}: {}",
            self.line, self.column, self.key, self.message
        )
    }
}

impl std::error::Error for ConfigError {}

/// Read a configuration file and enforce its byte and UTF-8 bounds.
pub(crate) fn read(path: &Path) -> Result<Config, ConfigError> {
    let file = File::open(path)
        .map_err(|error| ConfigError::file(format!("cannot open {path:?}: {error}")))?;
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

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    probe: Probe,
    limits: Limits,
    store: StoreTable,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Probe {
    levels: Spanned<Vec<String>>,
    tick_ms: Spanned<u32>,
    cgroup_root: Spanned<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Limits {
    max_rows: Spanned<u32>,
    annotate_rate_per_s: Spanned<u32>,
    annotate_burst: Spanned<u32>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StoreTable {
    dir: Spanned<String>,
    tmpfs_dir: Spanned<String>,
    events_cap_bytes: Spanned<u64>,
    events_ring_bytes: Spanned<u64>,
}

fn parse(text: &str) -> Result<Config, ConfigError> {
    let document: Document =
        toml::from_str(text).map_err(|error| ConfigError::toml(text, error))?;
    let levels = configured_levels(text, &document.probe.levels)?;
    let tick_ms = *document.probe.tick_ms.get_ref();
    if !(100..=60_000).contains(&tick_ms) {
        return Err(ConfigError::semantic(
            text,
            &document.probe.tick_ms.span(),
            KEY_TICK_MS,
            "must be in 100..=60000",
        ));
    }
    let cgroup_root = document.probe.cgroup_root.get_ref();
    let path = Path::new(&cgroup_root);
    if !path.is_absolute()
        || path
            .components()
            .any(|component| component == Component::ParentDir)
    {
        return Err(ConfigError::semantic(
            text,
            &document.probe.cgroup_root.span(),
            KEY_CGROUP_ROOT,
            "must be absolute and contain no .. component",
        ));
    }
    if cgroup_root.chars().any(char::is_control) {
        return Err(ConfigError::semantic(
            text,
            &document.probe.cgroup_root.span(),
            KEY_CGROUP_ROOT,
            "must contain no NUL or control character",
        ));
    }
    let max_rows = *document.limits.max_rows.get_ref();
    if !(1..=4_096).contains(&max_rows) {
        return Err(ConfigError::semantic(
            text,
            &document.limits.max_rows.span(),
            KEY_MAX_ROWS,
            "must be in 1..=4096",
        ));
    }
    let annotate_rate_per_s = *document.limits.annotate_rate_per_s.get_ref();
    if !(1..=1000).contains(&annotate_rate_per_s) {
        return Err(ConfigError::semantic(
            text,
            &document.limits.annotate_rate_per_s.span(),
            KEY_ANNOTATE_RATE,
            "must be in 1..=1000",
        ));
    }
    let annotate_burst = *document.limits.annotate_burst.get_ref();
    if !(1..=1024).contains(&annotate_burst) {
        return Err(ConfigError::semantic(
            text,
            &document.limits.annotate_burst.span(),
            KEY_ANNOTATE_BURST,
            "must be in 1..=1024",
        ));
    }
    let store = parse_store(text, document.store)?;

    Ok(Config {
        levels,
        tick_ms,
        cgroup_root: PathBuf::from(cgroup_root),
        max_rows,
        annotate_rate_per_s,
        annotate_burst,
        store,
    })
}

fn parse_store(source: &str, table: StoreTable) -> Result<StoreSettings, ConfigError> {
    let dir = store_path(source, &table.dir, KEY_STORE_DIR)?;
    let tmpfs_dir = store_path(source, &table.tmpfs_dir, KEY_STORE_TMPFS_DIR)?;
    if dir == tmpfs_dir || dir.starts_with(&tmpfs_dir) || tmpfs_dir.starts_with(&dir) {
        return Err(ConfigError::semantic(
            source,
            &table.tmpfs_dir.span(),
            KEY_STORE_TMPFS_DIR,
            "must differ from store.dir and neither path may contain the other",
        ));
    }

    let events_cap_bytes = *table.events_cap_bytes.get_ref();
    if !(1..=MAX_CAP_BYTES).contains(&events_cap_bytes) {
        return Err(ConfigError::semantic(
            source,
            &table.events_cap_bytes.span(),
            KEY_EVENTS_CAP_BYTES,
            format!("must be in 1..={MAX_CAP_BYTES}"),
        ));
    }
    let events_ring_bytes = *table.events_ring_bytes.get_ref();
    if !(4_096..=MAX_RING_BYTES).contains(&events_ring_bytes) {
        return Err(ConfigError::semantic(
            source,
            &table.events_ring_bytes.span(),
            KEY_EVENTS_RING_BYTES,
            format!("must be in 4096..={MAX_RING_BYTES}"),
        ));
    }

    let mut events = StoreConfig::plan_v4(&dir, 0, 0)
        .map_err(|_| {
            ConfigError::semantic(
                source,
                &table.events_cap_bytes.span(),
                KEY_EVENTS_CAP_BYTES,
                "cannot construct the events tier with this cap",
            )
        })?
        .events;
    events.cap_bytes = events_cap_bytes;
    let events = events.with_max_payload(EVENT_MAX_PAYLOAD).map_err(|_| {
        ConfigError::semantic(
            source,
            &table.events_cap_bytes.span(),
            KEY_EVENTS_CAP_BYTES,
            "cannot construct the events tier with this cap",
        )
    })?;
    let segment_bound = events.segment_file_bound();
    if events_cap_bytes < segment_bound {
        return Err(ConfigError::semantic(
            source,
            &table.events_cap_bytes.span(),
            KEY_EVENTS_CAP_BYTES,
            format!("must be at least {segment_bound} bytes for the events segment bound"),
        ));
    }

    Ok(StoreSettings {
        dir,
        tmpfs_dir,
        events_cap_bytes,
        events_ring_bytes,
    })
}

fn store_path(
    source: &str,
    value: &Spanned<String>,
    key: &'static str,
) -> Result<PathBuf, ConfigError> {
    let text = value.get_ref();
    let path = Path::new(text);
    if !path.is_absolute()
        || path
            .components()
            .any(|component| component == Component::ParentDir)
    {
        return Err(ConfigError::semantic(
            source,
            &value.span(),
            key,
            "must be absolute and contain no .. component",
        ));
    }
    if text.chars().any(char::is_control) {
        return Err(ConfigError::semantic(
            source,
            &value.span(),
            key,
            "must contain no NUL or control character",
        ));
    }
    Ok(path.to_path_buf())
}

fn configured_levels(
    source: &str,
    names: &Spanned<Vec<String>>,
) -> Result<Vec<Level>, ConfigError> {
    let span = names.span();
    let names = names.get_ref();
    if names.is_empty() {
        return Err(ConfigError::semantic(
            source,
            &span,
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
                return Err(ConfigError::semantic(
                    source,
                    &span,
                    KEY_LEVELS,
                    format!("{name:?} is not a probe-served level"),
                ));
            }
        };
        if !seen.insert(level) {
            return Err(ConfigError::semantic(
                source,
                &span,
                KEY_LEVELS,
                format!("duplicate level {name:?}"),
            ));
        }
        levels.push(level);
    }
    Ok(levels)
}

fn position(source: &str, offset: usize) -> (usize, usize) {
    let mut offset = offset.min(source.len());
    while !source.is_char_boundary(offset) {
        offset = offset.saturating_sub(1);
    }
    let line_start = source
        .get(..offset)
        .and_then(|prefix| prefix.rfind('\n'))
        .map_or(0, |newline| newline.saturating_add(1));
    let line = source.get(..line_start).map_or(1, |prefix| {
        prefix.bytes().filter(|byte| *byte == b'\n').count() + 1
    });
    let column = source
        .get(line_start..offset)
        .map_or(1, |prefix| prefix.chars().count() + 1);
    (line, column)
}

fn one_line(message: &str) -> String {
    let text = message.split_whitespace().collect::<Vec<_>>().join(" ");
    if text.is_empty() {
        String::from("TOML refused the document")
    } else {
        text
    }
}

fn named_field(message: &str) -> Option<String> {
    let mut parts = message.split('`');
    let field = parts.nth(1)?;
    if field.is_empty()
        || !field
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, '_' | '.'))
    {
        return None;
    }
    field.rsplit('.').next().map(str::to_string)
}

fn key_at(source: &str, offset: usize) -> String {
    let start = source
        .get(..offset)
        .and_then(|prefix| prefix.rfind('\n'))
        .map_or(0, |newline| newline.saturating_add(1));
    let end = source
        .get(offset..)
        .and_then(|suffix| suffix.find('\n'))
        .map_or(source.len(), |newline| offset.saturating_add(newline));
    let line = source.get(start..end).unwrap_or_default().trim();
    if let Some(section) = line
        .strip_prefix('[')
        .and_then(|rest| rest.split_once(']').map(|(name, _)| name))
    {
        return section.trim().to_string();
    }
    line.split_once('=')
        .map(|(key, _)| key.trim().to_string())
        .filter(|key| !key.is_empty())
        .unwrap_or_else(|| String::from("<toml>"))
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
        ServerLimits, StoreSettings, MAX_CAP_BYTES, MAX_CONFIG_BYTES, MAX_RING_BYTES,
    };

    const EXAMPLE: &str = r#"[probe]
levels = ["observe", "operator"]   # served levels
tick_ms = 1000
cgroup_root = "/sys/fs/cgroup"     # absolute, no ..

[limits]
max_rows = 1024
annotate_rate_per_s = 50
annotate_burst = 64

[store]
dir = "/var/lib/hostobs/probe"
tmpfs_dir = "/run/hostobs/tiers"
events_cap_bytes = 67108864
events_ring_bytes = 4194304
"#;

    const TEST_STORE: &str = r#"
[store]
dir = "/var/lib/hostobs/probe"
tmpfs_dir = "/run/hostobs/tiers"
events_cap_bytes = 67108864
events_ring_bytes = 4194304
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
        let complete = with_store(text);
        let error = parse(&complete).expect_err("configuration must be refused");
        let text = one_line(error);
        assert!(text.contains(line), "missing line {line:?}: {text}");
        assert!(text.contains(key), "missing key {key:?}: {text}");
    }

    fn missing_key_refusal(text: &str, key: &str) {
        let complete = with_store(text);
        let error = one_line(parse(&complete).expect_err("configuration must be refused"));
        assert!(error.contains(key), "missing key {key:?}: {error}");
    }

    fn with_store(text: &str) -> String {
        let mut complete = if text.contains("[store]") {
            text.to_string()
        } else {
            format!("{text}{TEST_STORE}")
        };
        if complete.contains("[limits]") && !complete.contains("annotate_rate_per_s") {
            let insert_at = complete.find("\n[store]").unwrap_or(complete.len());
            complete.insert_str(
                insert_at,
                "\nannotate_rate_per_s = 50\nannotate_burst = 64\n",
            );
        }
        complete
    }

    #[test]
    fn the_shipped_configuration_is_accepted_and_serves_the_three_probe_levels() {
        let config =
            parse(include_str!("../../../deploy/hostobsd.toml")).expect("deploy/hostobsd.toml");
        assert_eq!(
            config.levels,
            vec![Level::Observe, Level::Operator, Level::Observer]
        );
    }

    #[test]
    fn the_documented_example_parses_and_sets_probe_wide_limits_and_explicit_store_paths() {
        let config = parse(EXAMPLE).expect("example configuration");
        assert_eq!(
            config,
            Config {
                levels: vec![Level::Observe, Level::Operator],
                tick_ms: 1_000,
                cgroup_root: "/sys/fs/cgroup".into(),
                max_rows: 1_024,
                annotate_rate_per_s: 50,
                annotate_burst: 64,
                store: StoreSettings {
                    dir: "/var/lib/hostobs/probe".into(),
                    tmpfs_dir: "/run/hostobs/tiers".into(),
                    events_cap_bytes: 67_108_864,
                    events_ring_bytes: 4_194_304,
                },
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
    fn all_four_store_keys_are_required_and_unknown_store_keys_are_refused() {
        for (key, value) in [
            ("dir", "\"/var/lib/hostobs/probe\""),
            ("tmpfs_dir", "\"/run/hostobs/tiers\""),
            ("events_cap_bytes", "67108864"),
            ("events_ring_bytes", "4194304"),
        ] {
            let line = format!("{key} = {value}\n");
            let text = EXAMPLE.replace(&line, "");
            missing_key_refusal(&text, key);
        }
        let without_store = EXAMPLE
            .split("\n[store]\n")
            .next()
            .expect("store separator");
        let missing_table = one_line(parse(without_store).expect_err("store table is required"));
        assert!(missing_table.contains("store"), "{missing_table}");
        let text = format!("{EXAMPLE}unexpected = 1\n");
        parse_refusal(&text, "line 14", "unexpected");
    }

    #[test]
    fn store_paths_are_absolute_control_free_distinct_and_not_nested() {
        for (text, key) in [
            (
                EXAMPLE.replace("/var/lib/hostobs/probe", "relative/store"),
                "store.dir",
            ),
            (
                EXAMPLE.replace("/run/hostobs/tiers", "/run/hostobs/../tiers"),
                "store.tmpfs_dir",
            ),
            (
                EXAMPLE.replace("/run/hostobs/tiers", "/var/lib/hostobs/probe/tiers"),
                "store.tmpfs_dir",
            ),
            (
                EXAMPLE.replace("/run/hostobs/tiers", "/var/lib/hostobs/probe"),
                "store.tmpfs_dir",
            ),
            (
                EXAMPLE.replace("/run/hostobs/tiers", "/run/hostobs/\\u0001tiers"),
                "store.tmpfs_dir",
            ),
        ] {
            let error = one_line(parse(&text).expect_err("invalid store path"));
            assert!(error.contains(key), "missing {key:?}: {error}");
        }
    }

    #[test]
    fn store_numeric_bounds_and_the_constructed_events_segment_bound_are_enforced() {
        for cap in [String::from("0"), (MAX_CAP_BYTES + 1).to_string()] {
            let text = EXAMPLE.replace(
                "events_cap_bytes = 67108864",
                &format!("events_cap_bytes = {cap}"),
            );
            let error = one_line(parse(&text).expect_err("invalid events cap"));
            assert!(error.contains("store.events_cap_bytes"), "{error}");
        }
        for ring in [String::from("4095"), (MAX_RING_BYTES + 1).to_string()] {
            let text = EXAMPLE.replace(
                "events_ring_bytes = 4194304",
                &format!("events_ring_bytes = {ring}"),
            );
            let error = one_line(parse(&text).expect_err("invalid events ring"));
            assert!(error.contains("store.events_ring_bytes"), "{error}");
        }
        let config = parse(EXAMPLE).expect("example configuration");
        let bound = config
            .store_config(88)
            .expect("planned store")
            .events
            .segment_file_bound();
        let below_bound = bound.saturating_sub(1).to_string();
        let text = EXAMPLE.replace(
            "events_cap_bytes = 67108864",
            &format!("events_cap_bytes = {below_bound}"),
        );
        let error = one_line(parse(&text).expect_err("cap below segment bound"));
        assert!(error.contains("store.events_cap_bytes"), "{error}");
    }

    #[test]
    fn store_config_uses_named_plan_values_and_the_explicit_tier_paths() {
        let config = parse(EXAMPLE).expect("example configuration");
        let store = config.store_config(88).expect("store config");
        assert_eq!(store.epoch, 88);
        assert_eq!(store.dict_generation, 0);
        assert_eq!(
            store.dirs.tmpfs,
            Some(Path::new("/run/hostobs/tiers").into())
        );
        assert_eq!(
            store.dirs.cold.as_path(),
            Path::new("/var/lib/hostobs/probe/cold")
        );
        assert_eq!(
            store.dirs.events.as_path(),
            Path::new("/var/lib/hostobs/probe/events")
        );
        assert_eq!(store.events.cap_bytes, 67_108_864);
        assert_eq!(store.events.max_payload, 16 * 1024);
        assert_eq!(store.events_ring_bytes, 4_194_304);
        assert_eq!(store.writer_queue_bytes, 8 << 20);
        assert_eq!(store.dict_active_max, 65_536);
        assert_eq!(store.max_file_bytes, 96 << 20);
        assert_eq!(store.tiers[0].name, "hot");
        assert_eq!(store.tiers[1].name, "warm");
        assert_eq!(store.tiers[2].name, "cold");
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
            parse(&with_store(text)).is_err(),
            "observe#1 is still not a served level"
        );

        let text = r#"[probe]
levels = ["observe"] # allowed
tick_ms = 100
cgroup_root = "/tmp/#not-a-comment"
[limits]
max_rows = 1
"#;
        let config = parse(&with_store(text)).expect("hash inside a string is data");
        assert_eq!(config.cgroup_root, Path::new("/tmp/#not-a-comment"));
    }

    #[test]
    fn unknown_sections_keys_and_per_level_probe_wide_forms_are_refused() {
        for (text, line, key) in [
            (
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n[limits]\nmax_rows=1\n[limits.observe]\nmax_rows=2\n",
                "line 7",
                "observe",
            ),
            (
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\nlimts.max_rows=1\n[limits]\nmax_rows=1\n",
                "line 5",
                "limts",
            ),
            (
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n[limits]\nmax_rows={value=1}\n",
                "line 6",
                "max_rows",
            ),
        ] {
            parse_refusal(text, line, key);
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
            "probe",
        );
    }

    #[test]
    fn every_required_key_is_checked_when_absent() {
        for (key, text) in [
            (
                "levels",
                "[probe]\ntick_ms=100\ncgroup_root=\"/\"\n[limits]\nmax_rows=1\n",
            ),
            (
                "tick_ms",
                "[probe]\nlevels=[\"observe\"]\ncgroup_root=\"/\"\n[limits]\nmax_rows=1\n",
            ),
            (
                "cgroup_root",
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\n[limits]\nmax_rows=1\n",
            ),
            (
                "max_rows",
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n[limits]\n",
            ),
            (
                "annotate_rate_per_s",
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n\
                 [limits]\nmax_rows=1\nannotate_burst=64\n",
            ),
            (
                "annotate_burst",
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n\
                 [limits]\nmax_rows=1\nannotate_rate_per_s=50\n",
            ),
        ] {
            let complete = if key.starts_with("annotate_") {
                format!("{text}{TEST_STORE}")
            } else {
                with_store(text)
            };
            let error = one_line(parse(&complete).expect_err("required key is missing"));
            assert!(error.contains(key), "missing {key:?}: {error}");
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
        ] {
            let text = format!(
                "[probe]\nlevels={levels}\ntick_ms=100\ncgroup_root=\"/\"\n\
                 [limits]\nmax_rows=1\n"
            );
            parse_refusal(&text, "line 2", "levels");
        }
    }

    #[test]
    fn integer_ranges_are_strict_and_valid_toml_spellings_are_accepted() {
        for tick in ["99", "60001", "-1", "0100"] {
            let text = format!(
                "[probe]\nlevels=[\"observe\"]\ntick_ms={tick}\ncgroup_root=\"/\"\n\
                 [limits]\nmax_rows=1\n"
            );
            parse_refusal(&text, "line 3", "tick_ms");
        }
        for rows in ["0", "4097", "-1", "01"] {
            let text = format!(
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n\
                 [limits]\nmax_rows={rows}\n"
            );
            parse_refusal(&text, "line 6", "max_rows");
        }
        for (tick, expected) in [("+100", 100), ("1_000", 1_000)] {
            let text = format!(
                "[probe]\nlevels=[\"observe\"]\ntick_ms={tick}\ncgroup_root=\"/\"\n\
                 [limits]\nmax_rows=1\n"
            );
            assert_eq!(
                parse(&with_store(&text))
                    .expect("valid TOML integer")
                    .tick_ms,
                expected
            );
        }
        for (rows, expected) in [("+1", 1), ("1_024", 1_024)] {
            let text = format!(
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n\
                 [limits]\nmax_rows={rows}\n"
            );
            assert_eq!(
                parse(&with_store(&text))
                    .expect("valid TOML integer")
                    .max_rows,
                expected
            );
        }
        for (key, original, invalid) in [
            ("annotate_rate_per_s", "50", ["0", "1001", "-1", "01"]),
            ("annotate_burst", "64", ["0", "1025", "-1", "01"]),
        ] {
            for value in invalid {
                let text =
                    EXAMPLE.replace(&format!("{key} = {original}"), &format!("{key} = {value}"));
                let error = one_line(parse(&text).expect_err("annotation limit out of range"));
                assert!(error.contains(key), "missing {key:?}: {error}");
            }
        }
    }

    #[test]
    fn a_trailing_array_comma_is_valid_toml() {
        let text = "[probe]\nlevels=[\"observe\", \"operator\",]\ntick_ms=100\n\
                    cgroup_root=\"/\"\n[limits]\nmax_rows=1\n";
        assert_eq!(
            parse(&with_store(text)).expect("valid TOML array").levels,
            [Level::Observe, Level::Operator]
        );
    }

    #[test]
    fn type_and_overwide_integer_refusals_name_each_key_at_its_line() {
        for (text, line, key) in [
            (
                "[probe]\nlevels=[\"observe\"]\ntick_ms=true\ncgroup_root=\"/\"\n[limits]\nmax_rows=1\n",
                "line 3",
                "tick_ms",
            ),
            (
                "[probe]\nlevels=[\"observe\"]\ntick_ms=99999999999\ncgroup_root=\"/\"\n[limits]\nmax_rows=1\n",
                "line 3",
                "tick_ms",
            ),
            (
                "[probe]\nlevels=[\"observe\"]\ntick_ms=100\ncgroup_root=\"/\"\n[limits]\nmax_rows=1.5\n",
                "line 6",
                "max_rows",
            ),
            (
                "[probe]\nlevels=\"observe\"\ntick_ms=100\ncgroup_root=\"/\"\n[limits]\nmax_rows=1\n",
                "line 2",
                "levels",
            ),
            (
                "[probe]\nlevels=[\"observe\", true]\ntick_ms=100\ncgroup_root=\"/\"\n[limits]\nmax_rows=1\n",
                "line 2",
                "levels",
            ),
        ] {
            parse_refusal(text, line, key);
        }
    }

    #[test]
    fn cgroup_root_refuses_relative_parent_control_and_trailing_text() {
        for root in [
            r#""relative""#,
            r#""""#,
            r#""/tmp/../etc""#,
            r#""/tmp/a\b""#,
            r#""/tmp/\u0000""#,
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
