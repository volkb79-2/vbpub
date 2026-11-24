//! Adoption of the systemd `LISTEN_FDS` descriptors.
//!
//! The probe receives one already-bound `AF_UNIX` stream listener per served level. This
//! module is the only code in the workspace that claims a raw descriptor, so it keeps the
//! systemd contract in one audited place:
//!
//! - `LISTEN_PID` must be this process.
//! - `LISTEN_FDS` is the count of descriptors starting at fd 3, bounded to the three probe
//!   levels.
//! - `LISTEN_FDNAMES` must name exactly that many non-empty, distinct descriptors.
//! - Each descriptor must be an open, listening `AF_UNIX` `SOCK_STREAM` socket.
//!
//! [`take`] is process-wide and intentionally one-shot. It checks and sets the guard before
//! parsing, so a failed first call cannot be retried after some environment fact changes.

use std::collections::BTreeSet;
use std::fmt;
use std::os::fd::{FromRawFd, OwnedFd, RawFd};
use std::os::unix::net::UnixListener;
use std::sync::atomic::{AtomicBool, Ordering};

use rustix::io::FdFlags;
use rustix::net::{AddressFamily, SocketType};

const LISTEN_PID: &str = "LISTEN_PID";
const LISTEN_FDS: &str = "LISTEN_FDS";
const LISTEN_FDNAMES: &str = "LISTEN_FDNAMES";
const FIRST_LISTEN_FD: RawFd = 3;
const MAX_LISTEN_FDS: u32 = 3;

static TAKEN: AtomicBool = AtomicBool::new(false);

/// A refusal to parse or adopt the systemd activation environment.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ActivationError {
    /// A required systemd variable was absent.
    MissingVariable(&'static str),
    /// A systemd variable was present but not a strict decimal integer.
    InvalidVariable(&'static str),
    /// `LISTEN_PID` named a process other than this one.
    PidMismatch {
        /// The value systemd passed.
        listen_pid: u32,
        /// This process's pid.
        my_pid: u32,
    },
    /// `LISTEN_FDS` was zero or exceeded the probe's three levels.
    FdCount(u32),
    /// The number of names did not equal `LISTEN_FDS`.
    NameCount {
        /// The descriptor count.
        fds: u32,
        /// The number of colon-separated names.
        names: usize,
    },
    /// A name at this zero-based position was empty.
    EmptyName(usize),
    /// The same descriptor name appeared more than once.
    DuplicateName(String),
    /// [`take`] had already been called, successfully or not.
    AlreadyTaken,
    /// The descriptor was not an open socket.
    NotASocket(RawFd),
    /// The descriptor was a socket but not an `AF_UNIX` socket.
    NotUnix {
        /// The descriptor number.
        fd: RawFd,
    },
    /// The descriptor was not a stream socket.
    NotStream {
        /// The descriptor number.
        fd: RawFd,
    },
    /// The descriptor was a stream socket but was not listening.
    NotListening {
        /// The descriptor number.
        fd: RawFd,
    },
    /// An fd operation failed after ownership had been transferred.
    FdOperation {
        /// The descriptor number.
        fd: RawFd,
        /// The failed operation.
        operation: &'static str,
    },
}

impl fmt::Display for ActivationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ActivationError::MissingVariable(variable) => {
                write!(f, "{variable} is required")
            }
            ActivationError::InvalidVariable(variable) => {
                write!(f, "{variable} must be a decimal integer")
            }
            ActivationError::PidMismatch { listen_pid, my_pid } => write!(
                f,
                "{LISTEN_PID} is {listen_pid}, but this process is {my_pid}"
            ),
            ActivationError::FdCount(fds) => {
                write!(f, "{LISTEN_FDS} is {fds}, expected 1..={MAX_LISTEN_FDS}")
            }
            ActivationError::NameCount { fds, names } => write!(
                f,
                "{LISTEN_FDNAMES} has {names} names for {LISTEN_FDS}={fds}"
            ),
            ActivationError::EmptyName(index) => {
                write!(f, "{LISTEN_FDNAMES} name {index} is empty")
            }
            ActivationError::DuplicateName(name) => {
                write!(f, "{LISTEN_FDNAMES} repeats the name {name:?}")
            }
            ActivationError::AlreadyTaken => {
                write!(f, "{LISTEN_FDS} has already been taken")
            }
            ActivationError::NotASocket(fd) => {
                write!(f, "{LISTEN_FDS} fd {fd} is not an open socket")
            }
            ActivationError::NotUnix { fd } => {
                write!(f, "{LISTEN_FDS} fd {fd} is not AF_UNIX")
            }
            ActivationError::NotStream { fd } => {
                write!(f, "{LISTEN_FDS} fd {fd} is not SOCK_STREAM")
            }
            ActivationError::NotListening { fd } => {
                write!(f, "{LISTEN_FDS} fd {fd} is not listening")
            }
            ActivationError::FdOperation { fd, operation } => {
                write!(f, "{LISTEN_FDS} fd {fd} could not be {operation}")
            }
        }
    }
}

impl std::error::Error for ActivationError {}

/// Adopt the named systemd listeners, once per process.
///
/// The four arguments are the three environment values and this process's pid. Passing
/// `None` for a variable represents its absence; the caller owns the UTF-8 conversion
/// from the environment.
///
/// # Errors
///
/// Every malformed environment variable, wrong pid, bad inconsistency between names and
/// descriptors, invalid descriptor, or repeated call is an [`ActivationError`]. Once this
/// function has been called, later calls fail with
/// [`ActivationError::AlreadyTaken`] even if the first call failed.
pub fn take(
    listen_pid: Option<&str>,
    listen_fds: Option<&str>,
    listen_fdnames: Option<&str>,
    my_pid: u32,
) -> Result<Vec<(String, UnixListener)>, ActivationError> {
    if TAKEN
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return Err(ActivationError::AlreadyTaken);
    }
    let parsed = parse(listen_pid, listen_fds, listen_fdnames, my_pid)?;
    let mut out = Vec::with_capacity(parsed.len());
    for (fd, name) in parsed {
        out.push((name, adopt_raw(fd)?));
    }
    Ok(out)
}

fn parse(
    listen_pid: Option<&str>,
    listen_fds: Option<&str>,
    listen_fdnames: Option<&str>,
    my_pid: u32,
) -> Result<Vec<(RawFd, String)>, ActivationError> {
    let listen_pid = listen_pid.ok_or(ActivationError::MissingVariable(LISTEN_PID))?;
    let listen_fds = listen_fds.ok_or(ActivationError::MissingVariable(LISTEN_FDS))?;
    let listen_fdnames = listen_fdnames.ok_or(ActivationError::MissingVariable(LISTEN_FDNAMES))?;

    let pid = parse_decimal(listen_pid, LISTEN_PID)?;
    if pid != my_pid {
        return Err(ActivationError::PidMismatch {
            listen_pid: pid,
            my_pid,
        });
    }
    let fds = parse_decimal(listen_fds, LISTEN_FDS)?;
    if !(1..=MAX_LISTEN_FDS).contains(&fds) {
        return Err(ActivationError::FdCount(fds));
    }

    let names: Vec<&str> = listen_fdnames.split(':').collect();
    if u32::try_from(names.len()).unwrap_or(u32::MAX) != fds {
        return Err(ActivationError::NameCount {
            fds,
            names: names.len(),
        });
    }
    let mut unique = BTreeSet::new();
    for (index, name) in names.iter().enumerate() {
        if name.is_empty() {
            return Err(ActivationError::EmptyName(index));
        }
        if !unique.insert(*name) {
            return Err(ActivationError::DuplicateName(name.to_string()));
        }
    }

    Ok(names
        .into_iter()
        .enumerate()
        .map(|(index, name)| {
            (
                FIRST_LISTEN_FD.saturating_add(RawFd::try_from(index).unwrap_or(RawFd::MAX)),
                name.to_string(),
            )
        })
        .collect())
}

fn parse_decimal(text: &str, variable: &'static str) -> Result<u32, ActivationError> {
    if text.is_empty() || !text.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(ActivationError::InvalidVariable(variable));
    }
    text.parse()
        .map_err(|_| ActivationError::InvalidVariable(variable))
}

#[allow(unsafe_code)]
fn adopt_raw(fd: RawFd) -> Result<UnixListener, ActivationError> {
    let target = std::fs::read_link(format!("/proc/self/fd/{fd}"))
        .map_err(|_| ActivationError::NotASocket(fd))?;
    if !target
        .as_os_str()
        .as_encoded_bytes()
        .starts_with(b"socket:")
    {
        return Err(ActivationError::NotASocket(fd));
    }

    // SAFETY: `LISTEN_PID` equals this process, `fd` lies in systemd's fd 3.. range, and
    // production reaches this only through `take` after its check-and-set guard. Tests
    // transfer ownership explicitly with `into_raw_fd`. No other owner exists, and the
    // socket check above proves the descriptor is open.
    let owned = unsafe { OwnedFd::from_raw_fd(fd) };
    if rustix::net::sockopt::socket_domain(&owned) != Ok(AddressFamily::UNIX) {
        return Err(ActivationError::NotUnix { fd });
    }
    if rustix::net::sockopt::socket_type(&owned) != Ok(SocketType::STREAM) {
        return Err(ActivationError::NotStream { fd });
    }
    if rustix::net::sockopt::socket_acceptconn(&owned) != Ok(true) {
        return Err(ActivationError::NotListening { fd });
    }
    rustix::io::fcntl_setfd(&owned, FdFlags::CLOEXEC).map_err(|_| {
        ActivationError::FdOperation {
            fd,
            operation: "marked close-on-exec",
        }
    })?;
    let listener = UnixListener::from(owned);
    listener
        .set_nonblocking(true)
        .map_err(|_| ActivationError::FdOperation {
            fd,
            operation: "made non-blocking",
        })?;
    Ok(listener)
}

#[cfg(test)]
mod tests {
    use std::fs::File;
    use std::os::fd::{AsRawFd, IntoRawFd};
    use std::os::linux::net::SocketAddrExt;
    use std::os::unix::net::{SocketAddr, UnixDatagram, UnixListener, UnixStream};
    use std::sync::atomic::{AtomicU32, Ordering};

    use super::{adopt_raw, parse, take, ActivationError, FIRST_LISTEN_FD};

    static NEXT_NAME: AtomicU32 = AtomicU32::new(0);

    fn abstract_listener() -> UnixListener {
        let serial = NEXT_NAME.fetch_add(1, Ordering::Relaxed);
        let name = format!("hostobs-activation-{}-{serial}", std::process::id());
        let address =
            SocketAddr::from_abstract_name(name.as_bytes()).expect("abstract socket address");
        UnixListener::bind_addr(&address).expect("bind abstract listener")
    }

    fn parse_one(name: &str) -> Result<Vec<(i32, String)>, ActivationError> {
        parse(Some("41"), Some("1"), Some(name), 41)
    }

    #[test]
    fn parse_adopts_one_two_and_three_named_descriptors() {
        for (fds, names) in [
            ("1", "observe"),
            ("2", "observe:operator"),
            ("3", "observe:operator:observer"),
        ] {
            let parsed = parse(Some("41"), Some(fds), Some(names), 41).expect("valid activation");
            assert_eq!(parsed.len(), names.split(':').count());
            for (index, (fd, name)) in parsed.into_iter().enumerate() {
                let offset = i32::try_from(index).expect("at most three descriptors");
                assert_eq!(fd, FIRST_LISTEN_FD.saturating_add(offset));
                assert_eq!(name, names.split(':').nth(index).expect("name"));
            }
        }
    }

    #[test]
    fn parse_refuses_every_environment_shape() {
        assert_eq!(
            parse(None, Some("1"), Some("observe"), 41),
            Err(ActivationError::MissingVariable("LISTEN_PID"))
        );
        assert_eq!(
            parse(Some("41"), None, Some("observe"), 41),
            Err(ActivationError::MissingVariable("LISTEN_FDS"))
        );
        assert_eq!(
            parse(Some("41"), Some("1"), None, 41),
            Err(ActivationError::MissingVariable("LISTEN_FDNAMES"))
        );
        assert_eq!(
            parse(Some("x"), Some("1"), Some("observe"), 41),
            Err(ActivationError::InvalidVariable("LISTEN_PID"))
        );
        assert_eq!(
            parse(Some("+41"), Some("1"), Some("observe"), 41),
            Err(ActivationError::InvalidVariable("LISTEN_PID"))
        );
        assert_eq!(
            parse(Some("41"), Some("zero"), Some("observe"), 41),
            Err(ActivationError::InvalidVariable("LISTEN_FDS"))
        );
        assert_eq!(
            parse(Some("42"), Some("1"), Some("observe"), 41),
            Err(ActivationError::PidMismatch {
                listen_pid: 42,
                my_pid: 41
            })
        );
        assert_eq!(
            parse(Some("41"), Some("0"), Some(""), 41),
            Err(ActivationError::FdCount(0))
        );
        assert_eq!(
            parse(Some("41"), Some("4"), Some("a:b:c:d"), 41),
            Err(ActivationError::FdCount(4))
        );
        assert_eq!(
            parse(Some("41"), Some("2"), Some("observe"), 41),
            Err(ActivationError::NameCount { fds: 2, names: 1 })
        );
        assert_eq!(parse_one(""), Err(ActivationError::EmptyName(0)));
        assert_eq!(
            parse(Some("41"), Some("3"), Some("observe::observer"), 41),
            Err(ActivationError::EmptyName(1))
        );
        assert_eq!(
            parse(Some("41"), Some("2"), Some("observe:observe"), 41),
            Err(ActivationError::DuplicateName(String::from("observe")))
        );
    }

    #[test]
    fn adopt_raw_adopts_a_listening_unix_stream() {
        let listener = abstract_listener();
        let expected = listener
            .local_addr()
            .expect("listening address")
            .as_abstract_name()
            .expect("abstract name")
            .to_vec();
        let fd = listener.into_raw_fd();
        let adopted = adopt_raw(fd).expect("listening socket is adoptable");
        assert_eq!(
            adopted
                .local_addr()
                .expect("named address")
                .as_abstract_name(),
            Some(expected.as_slice())
        );
        drop(adopted);
    }

    #[test]
    fn adopt_raw_refuses_a_connected_stream_pair_end() {
        let (left, _right) = UnixStream::pair().expect("socket pair");
        let fd = left.into_raw_fd();
        assert!(matches!(
            adopt_raw(fd),
            Err(ActivationError::NotListening { fd: got }) if got == fd
        ));
    }

    #[test]
    fn adopt_raw_refuses_a_unix_datagram() {
        let datagram = UnixDatagram::unbound().expect("unbound datagram");
        let fd = datagram.into_raw_fd();
        assert!(matches!(
            adopt_raw(fd),
            Err(ActivationError::NotStream { fd: got }) if got == fd
        ));
    }

    #[test]
    fn adopt_raw_refuses_a_read_only_file_without_taking_its_ownership() {
        let file = File::open("/dev/null").expect("read-only file");
        let fd = file.as_raw_fd();
        assert!(matches!(
            adopt_raw(fd),
            Err(ActivationError::NotASocket(got)) if got == fd
        ));
        file.metadata()
            .expect("the test still owns and can inspect the file");
    }

    #[test]
    fn take_is_one_shot_even_after_a_failed_first_call() {
        let pid = std::process::id();
        assert!(matches!(
            take(None, None, None, pid),
            Err(ActivationError::MissingVariable("LISTEN_PID"))
        ));
        assert!(matches!(
            take(Some(&pid.to_string()), Some("1"), Some("observe"), pid),
            Err(ActivationError::AlreadyTaken)
        ));
    }
}
