mod config;
mod driver;

use std::ffi::OsString;
use std::fmt;
use std::io;
use std::path::Path;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use hostobs_kernel::activation;
use hostobs_kernel::clock::{Clock, SystemClock, Timeline};
use hostobs_kernel::fs::RealFs;
use hostobs_kernel::identity::{read_boot_id, BootId, IdentityError};
use hostobs_probe::live::ProbeBackend;
use hostobs_probe::sampler::{self, Published, SamplerConfig, SamplerError};
use hostobs_serve::backend::Identity;
use hostobs_serve::server::Server;
use rustix::event::{eventfd, EventfdFlags};

use crate::config::{Action, ConfigError, LevelMapError};

const BOOT_ID_PATH: &str = "/proc/sys/kernel/random/boot_id";
const PROC_ROOT: &str = "/proc";
const MAX_BOOT_ID_BYTES: usize = 64;

fn main() {
    let code = match unicode_arguments() {
        Ok(arguments) => entry(&arguments),
        Err(error) => refuse(error),
    };
    std::process::exit(code);
}

fn unicode_arguments() -> Result<Vec<String>, StartupError> {
    std::env::args_os().skip(1).map(unicode_argument).collect()
}

fn unicode_argument(argument: OsString) -> Result<String, StartupError> {
    argument
        .into_string()
        .map_err(|_| StartupError::ArgumentNotUtf8)
}

/// Map one parsed command line to the process exit status, with no host side effects for
/// `--version` or argument refusals.
fn entry(arguments: &[String]) -> i32 {
    match config::parse_args(arguments) {
        Ok(Action::Version) => {
            println!("{}", version_text());
            0
        }
        Ok(Action::Run { config }) => match serve(&config) {
            Ok(()) => 1,
            Err(ProcessError::Startup(error)) => refuse(error),
            Err(ProcessError::Run(error)) => {
                eprintln!("hostobs-probe: server loop: {error}");
                1
            }
        },
        Err(error) => refuse(error),
    }
}

fn serve(config_path: &Path) -> Result<(), ProcessError> {
    let listen_pid = listen_variable("LISTEN_PID").map_err(ProcessError::Startup)?;
    let listen_fds = listen_variable("LISTEN_FDS").map_err(ProcessError::Startup)?;
    let listen_fdnames = listen_variable("LISTEN_FDNAMES").map_err(ProcessError::Startup)?;
    let adopted = activation::take(
        listen_pid.as_deref(),
        listen_fds.as_deref(),
        listen_fdnames.as_deref(),
        std::process::id(),
    )
    .map_err(|error| ProcessError::Startup(StartupError::Activation(error)))?;

    let config = config::read(config_path)
        .map_err(|error| ProcessError::Startup(StartupError::Config(error)))?;
    let listeners = config::map_levels(adopted, &config.levels)
        .map_err(|error| ProcessError::Startup(StartupError::Levels(error)))?;

    let boot_id = read_boot_id(&RealFs::new(), Path::new(BOOT_ID_PATH), MAX_BOOT_ID_BYTES)
        .map_err(|error| ProcessError::Startup(StartupError::BootId(error)))?;
    let boot_bytes = *boot_id.as_bytes();
    let epoch_clock = SystemClock::new(Timeline::new(boot_bytes, 0));
    let epoch = epoch_clock.boot_ns();
    let clock = SystemClock::new(Timeline::new(boot_bytes, epoch));
    let started_mono_ns = clock.mono_ns();
    let started_real_ns = clock.wall_ns();
    let identity = identity(&boot_id, epoch);

    let wake = eventfd(0, EventfdFlags::CLOEXEC | EventfdFlags::NONBLOCK).map_err(|error| {
        ProcessError::Startup(StartupError::Eventfd(io::Error::from_raw_os_error(
            error.raw_os_error(),
        )))
    })?;
    let sampler_wake = wake
        .try_clone()
        .map_err(|error| ProcessError::Startup(StartupError::WakeClone(error)))?;
    let limits = config.server_limits();
    let published = Arc::new(Published::new(started_mono_ns, started_real_ns));
    let sampler_cfg = SamplerConfig {
        boot_id: boot_bytes,
        epoch,
        tick_ms: config.tick_ms,
        cgroup_root: config.cgroup_root.clone(),
        max_rows: config.max_rows,
    };
    let stop = Arc::new(AtomicBool::new(false));
    let _sampler = sampler::spawn(
        sampler_cfg,
        Arc::clone(&published),
        sampler_wake,
        Arc::clone(&stop),
    )
    .map_err(|error| ProcessError::Startup(StartupError::Sampler(error)))?;
    let backend = ProbeBackend::new(identity, Arc::clone(&published));
    let server = Server::new(limits, backend);
    let mut now_ns = || clock.mono_ns();
    driver::run(
        server,
        listeners,
        wake,
        stop,
        Path::new(PROC_ROOT),
        &config.cgroup_root,
        &mut now_ns,
    )
    .map_err(ProcessError::Run)
}

fn listen_variable(name: &'static str) -> Result<Option<String>, StartupError> {
    listen_variable_result(name, std::env::var(name))
}

fn listen_variable_result(
    name: &'static str,
    result: Result<String, std::env::VarError>,
) -> Result<Option<String>, StartupError> {
    match result {
        Ok(value) => Ok(Some(value)),
        Err(std::env::VarError::NotPresent) => Ok(None),
        Err(std::env::VarError::NotUnicode(_)) => Err(StartupError::VariableNotUtf8(name)),
    }
}

fn version_text() -> String {
    format!(
        "{} {}",
        hostobs_probe::package_name(),
        env!("CARGO_PKG_VERSION")
    )
}

fn identity(boot_id: &BootId, epoch: u64) -> Identity {
    Identity {
        server: version_text(),
        boot_id: boot_id.to_string(),
        epoch,
        features: Vec::new(),
    }
}

fn refuse(error: impl fmt::Display) -> i32 {
    eprintln!("hostobs-probe: {error}");
    2
}

#[derive(Debug)]
enum StartupError {
    ArgumentNotUtf8,
    VariableNotUtf8(&'static str),
    Activation(activation::ActivationError),
    Config(ConfigError),
    Levels(LevelMapError),
    BootId(IdentityError),
    Eventfd(io::Error),
    WakeClone(io::Error),
    Sampler(SamplerError),
}

impl fmt::Display for StartupError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StartupError::ArgumentNotUtf8 => f.write_str("a command-line argument is not UTF-8"),
            StartupError::VariableNotUtf8(name) => write!(f, "{name} is not UTF-8"),
            StartupError::Activation(error) => error.fmt(f),
            StartupError::Config(error) => error.fmt(f),
            StartupError::Levels(error) => error.fmt(f),
            StartupError::BootId(error) => write!(f, "cannot read {BOOT_ID_PATH}: {error}"),
            StartupError::Eventfd(error) => write!(f, "cannot create wake eventfd: {error}"),
            StartupError::WakeClone(error) => write!(f, "cannot clone wake eventfd: {error}"),
            StartupError::Sampler(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for StartupError {}

enum ProcessError {
    Startup(StartupError),
    Run(io::Error),
}

#[cfg(test)]
mod tests {
    use std::ffi::OsString;
    use std::os::unix::ffi::OsStringExt;

    use hostobs_kernel::identity::BootId;

    use super::{entry, identity, listen_variable_result, unicode_argument, version_text};

    #[test]
    fn entry_only_maps_version_and_argument_decisions_without_starting() {
        assert_eq!(
            version_text(),
            format!(
                "{} {}",
                hostobs_probe::package_name(),
                env!("CARGO_PKG_VERSION")
            )
        );
        assert_eq!(entry(&[String::from("--version")]), 0);
        assert_eq!(entry(&[]), 2);
        assert_eq!(entry(&[String::from("--unknown")]), 2);
    }

    #[test]
    fn boot_identity_and_non_utf8_arguments_are_typed() {
        let boot = BootId::parse("00112233-4455-6677-8899-aabbccddeeff")
            .expect("the test boot id is valid");
        let found = identity(&boot, 17);
        assert_eq!(found.server, version_text());
        assert_eq!(found.boot_id, boot.to_string());
        assert_eq!(found.epoch, 17);
        assert!(found.features.is_empty());

        let invalid = OsString::from_vec(vec![0xff]);
        assert!(unicode_argument(invalid).is_err());
        assert_eq!(
            listen_variable_result("LISTEN_PID", Err(std::env::VarError::NotPresent))
                .expect("absence is passed to activation"),
            None
        );
        assert!(listen_variable_result(
            "LISTEN_FDS",
            Err(std::env::VarError::NotUnicode(OsString::from_vec(vec![
                0xff
            ])))
        )
        .is_err());
    }
}
