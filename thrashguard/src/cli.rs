//! The command line, and the two places a real host path is spelled (design §5 D9).
//!
//! `--cgroup-root` and `--pressure-path` exist so every test can inject a temp
//! tree; their *defaults* are the only place this crate names a host path, and
//! `tools/check-lints.py` asserts that allowance by count so it cannot grow.
//!
//! Verbs (design §3 "Standalone", §6d):
//!
//! ```text
//! thrashguard serve|status|candidates|events|version [--config P] [--cgroup-root P]
//!             [--pressure-path P] [--socket P] [--since-seq N] [--registrant-socket P]
//!             [--reader-socket P]
//! ```
//!
//! `serve` runs the loop; `status`, `candidates` and `events` either ask a running
//! daemon over `--socket` or answer locally from `--config`, which is what makes a
//! standalone host usable with no daemon, no fleetd and no registrant.

use std::fmt;
use std::path::PathBuf;

/// The production cgroup root — the one literal in the crate (see the module docs).
pub const DEFAULT_CGROUP_ROOT: &str = "/sys/fs/cgroup";
/// The production PSI path.
pub const DEFAULT_PRESSURE_PATH: &str = "/proc/pressure/memory";

/// What the process must do.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verb {
    /// Run the guard loop.
    Serve,
    /// `mode`, pressure, the candidate order and every parked unit.
    Status,
    /// The next tick's order with the victim it implies.
    Candidates,
    /// Journal rows above `--since-seq`.
    Events,
    /// The package and wire versions.
    Version,
}

impl Verb {
    /// The word the operator types.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Serve => "serve",
            Self::Status => "status",
            Self::Candidates => "candidates",
            Self::Events => "events",
            Self::Version => "version",
        }
    }

    /// The verb an operator typed, or `None`.
    pub fn from_name(name: &str) -> Option<Self> {
        match name {
            "serve" => Some(Self::Serve),
            "status" => Some(Self::Status),
            "candidates" => Some(Self::Candidates),
            "events" => Some(Self::Events),
            "version" | "--version" => Some(Self::Version),
            _ => None,
        }
    }

    /// Whether this verb needs the guard's decision (and therefore a config).
    pub fn needs_config(self) -> bool {
        matches!(self, Self::Serve | Self::Status | Self::Candidates)
    }
}

/// The parsed invocation.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Args {
    /// The verb.
    pub verb: Verb,
    /// Where the configuration is (`--config`).
    pub config: Option<PathBuf>,
    /// The injected cgroup root (`--cgroup-root`).
    pub cgroup_root: PathBuf,
    /// The injected pressure document (`--pressure-path`).
    pub pressure_path: PathBuf,
    /// A running daemon's reader socket, instead of answering locally (`--socket`).
    pub socket: Option<PathBuf>,
    /// The `events` paging mark (`--since-seq`).
    pub since_seq: u64,
    /// Where `serve` binds the registrant listener (`--registrant-socket`).
    pub registrant_socket: Option<PathBuf>,
    /// Where `serve` binds the reader listener (`--reader-socket`).
    pub reader_socket: Option<PathBuf>,
}

impl Args {
    /// The default invocation: `serve` with the host's own paths.
    pub fn defaults() -> Self {
        Self {
            verb: Verb::Serve,
            config: None,
            cgroup_root: PathBuf::from(DEFAULT_CGROUP_ROOT),
            pressure_path: PathBuf::from(DEFAULT_PRESSURE_PATH),
            socket: None,
            since_seq: 0,
            registrant_socket: None,
            reader_socket: None,
        }
    }
}

/// Why the invocation could not be understood.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CliError {
    /// No verb: the usage line follows.
    NoVerb,
    /// A verb this tool does not have.
    UnknownVerb(String),
    /// A flag that takes a value did not get one.
    MissingValue(String),
    /// A flag this verb does not accept.
    UnexpectedFlag(String),
    /// A value that must be a whole number is not.
    NotANumber {
        /// The flag.
        flag: String,
        /// What was given.
        value: String,
    },
    /// A flag was given twice with different values.
    Duplicate(String),
}

impl fmt::Display for CliError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoVerb => f.write_str("no verb given"),
            Self::UnknownVerb(verb) => write!(f, "unknown verb {verb:?}"),
            Self::MissingValue(flag) => write!(f, "{flag} needs a value"),
            Self::UnexpectedFlag(flag) => write!(f, "{flag} is not a flag this verb accepts"),
            Self::NotANumber { flag, value } => write!(f, "{flag} takes a whole number, not {value:?}"),
            Self::Duplicate(flag) => write!(f, "{flag} was given twice"),
        }
    }
}

impl std::error::Error for CliError {}

/// The one usage line every refusal refers to.
pub const USAGE: &str = "usage: thrashguard serve|status|candidates|events|version \
[--config P] [--cgroup-root P] [--pressure-path P] [--socket P] [--since-seq N] \
[--registrant-socket P] [--reader-socket P]";

/// Parse an argument vector (`argv[0]` is skipped by the caller).
pub fn parse<I: IntoIterator<Item = String>>(argv: I) -> Result<Args, CliError> {
    let mut args = Args::defaults();
    let mut iter = argv.into_iter();
    let mut verb = None;
    while let Some(token) = iter.next() {
        if let Some(name) = token.strip_prefix("--") {
            let (flag, inline) = match name.split_once('=') {
                Some((flag, value)) => (format!("--{flag}"), Some(value.to_string())),
                None => (format!("--{name}"), None),
            };
            if flag == "--version" {
                set_verb(&mut verb, Verb::Version)?;
                continue;
            }
            let value = match inline {
                Some(value) => value,
                None => iter.next().ok_or_else(|| CliError::MissingValue(flag.clone()))?,
            };
            match flag.as_str() {
                "--config" => set_once(&mut args.config, PathBuf::from(value), &flag)?,
                "--cgroup-root" => args.cgroup_root = PathBuf::from(value),
                "--pressure-path" => args.pressure_path = PathBuf::from(value),
                "--socket" => set_once(&mut args.socket, PathBuf::from(value), &flag)?,
                "--registrant-socket" => set_once(&mut args.registrant_socket, PathBuf::from(value), &flag)?,
                "--reader-socket" => set_once(&mut args.reader_socket, PathBuf::from(value), &flag)?,
                "--since-seq" => {
                    args.since_seq = value
                        .parse::<u64>()
                        .map_err(|_| CliError::NotANumber { flag: flag.clone(), value: value.clone() })?;
                }
                other => return Err(CliError::UnexpectedFlag(other.to_string())),
            }
            continue;
        }
        set_verb(&mut verb, Verb::from_name(&token).ok_or(CliError::UnknownVerb(token))?)?;
    }
    args.verb = verb.ok_or(CliError::NoVerb)?;
    Ok(args)
}

fn set_verb(slot: &mut Option<Verb>, verb: Verb) -> Result<(), CliError> {
    match slot {
        Some(existing) if *existing != verb => Err(CliError::Duplicate("verb".to_string())),
        Some(_) => Ok(()),
        None => {
            *slot = Some(verb);
            Ok(())
        }
    }
}

fn set_once<T>(slot: &mut Option<T>, value: T, flag: &str) -> Result<(), CliError> {
    if slot.is_some() {
        return Err(CliError::Duplicate(flag.to_string()));
    }
    *slot = Some(value);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vector(tokens: &[&str]) -> Vec<String> {
        tokens.iter().map(|token| (*token).to_string()).collect()
    }

    #[test]
    fn every_documented_invocation_parses() {
        let args = parse(vector(&["--version"])).expect("the version flag");
        assert_eq!(args.verb, Verb::Version);
        assert!(!args.verb.needs_config());
        let args = parse(vector(&["version"])).expect("the version verb");
        assert_eq!(args.verb, Verb::Version);
        let args = parse(vector(&["serve", "--config", "/etc/thrashguard/thrashguard.toml"]))
            .expect("serve with a config");
        assert_eq!(args.verb, Verb::Serve);
        assert_eq!(args.config, Some(PathBuf::from("/etc/thrashguard/thrashguard.toml")));
        assert!(args.verb.needs_config());
        let args = parse(vector(&["status", "--config=/etc/t/x.toml", "--cgroup-root", "/tmp/cg"]))
            .expect("equals form and an injected root");
        assert_eq!(args.verb, Verb::Status);
        assert_eq!(args.config, Some(PathBuf::from("/etc/t/x.toml")));
        assert_eq!(args.cgroup_root, PathBuf::from("/tmp/cg"));
        let args = parse(vector(&["events", "--socket", "/run/thrashguard/reader/thrashguard.sock", "--since-seq", "17"]))
            .expect("events over a socket");
        assert_eq!(args.since_seq, 17);
        assert_eq!(args.socket, Some(PathBuf::from("/run/thrashguard/reader/thrashguard.sock")));
        // The defaults are the host's own paths, and the injected ones win.
        let args = parse(vector(&["status"])).expect("a bare verb");
        assert_eq!(args.cgroup_root, PathBuf::from(DEFAULT_CGROUP_ROOT));
        assert_eq!(args.pressure_path, PathBuf::from(DEFAULT_PRESSURE_PATH));
        assert_eq!(args.socket, None, "no socket means answer locally");
        let args = parse(vector(&["status", "--pressure-path", "/tmp/p"])).expect("injected pressure");
        assert_eq!(args.pressure_path, PathBuf::from("/tmp/p"));
    }

    #[test]
    fn every_refusal_is_a_named_error_rather_than_a_guess() {
        let cases = [
            (vector(&[]), CliError::NoVerb),
            (vector(&["frobnicate"]), CliError::UnknownVerb("frobnicate".to_string())),
            (vector(&["status", "--config"]), CliError::MissingValue("--config".to_string())),
            (vector(&["status", "--nope", "x"]), CliError::UnexpectedFlag("--nope".to_string())),
            (
                vector(&["events", "--since-seq", "later"]),
                CliError::NotANumber { flag: "--since-seq".to_string(), value: "later".to_string() },
            ),
            (vector(&["status", "--config", "a", "--config", "b"]), CliError::Duplicate("--config".to_string())),
            (vector(&["status", "serve"]), CliError::Duplicate("verb".to_string())),
        ];
        for (argv, expected) in cases {
            assert_eq!(parse(argv.clone()).expect_err("refused"), expected, "{argv:?}");
        }
        assert!(USAGE.contains("status") && USAGE.contains("--cgroup-root"), "{USAGE}");
    }
}
