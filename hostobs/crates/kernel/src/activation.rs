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
//! - Accepted streams expose a raw `SO_PEERCRED` read; credential decoding stays with the probe.
//!
//! [`take`] is process-wide and intentionally one-shot. It checks and sets the guard before
//! parsing, so a failed first call cannot be retried after some environment fact changes.

use std::collections::BTreeSet;
use std::fmt;
use std::os::fd::RawFd;
use std::os::unix::net::UnixListener;
use std::sync::atomic::{AtomicBool, Ordering};

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
    NotASocket {
        /// The descriptor number.
        fd: RawFd,
        /// The `LISTEN_FDNAMES` name of the descriptor.
        name: String,
    },
    /// The descriptor was not the kind of socket systemd must pass.
    WrongSocket {
        /// The descriptor number.
        fd: RawFd,
        /// The `LISTEN_FDNAMES` name of the descriptor.
        name: String,
        /// The required socket property.
        want: &'static str,
    },
    /// An fd operation failed after ownership had been transferred.
    FdOperation {
        /// The descriptor number.
        fd: RawFd,
        /// The `LISTEN_FDNAMES` name of the descriptor.
        name: String,
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
            ActivationError::NotASocket { fd, name } => {
                write!(f, "{LISTEN_FDS} fd {fd} ({name:?}) is not an open socket")
            }
            ActivationError::WrongSocket { fd, name, want } => {
                write!(f, "{LISTEN_FDS} fd {fd} ({name:?}) is not {want}")
            }
            ActivationError::FdOperation {
                fd,
                name,
                operation,
            } => {
                write!(
                    f,
                    "{LISTEN_FDS} fd {fd} ({name:?}) could not be {operation}"
                )
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
        out.push((name.clone(), adopt_raw(fd, &name)?));
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
    if text.is_empty()
        || !text.bytes().all(|byte| byte.is_ascii_digit())
        || (text.len() > 1 && text.starts_with('0'))
    {
        return Err(ActivationError::InvalidVariable(variable));
    }
    text.parse()
        .map_err(|_| ActivationError::InvalidVariable(variable))
}

#[allow(unsafe_code)]
mod raw {
    use std::io;
    use std::os::fd::{AsFd, AsRawFd, FromRawFd, OwnedFd};
    use std::os::raw::{c_int, c_void};
    use std::os::unix::net::UnixListener;

    use rustix::io::FdFlags;
    use rustix::net::{AddressFamily, SocketType};

    use super::{ActivationError, RawFd};

    const SOL_SOCKET: c_int = 1;
    const SO_PEERCRED: c_int = 17;
    const UCRED_BYTES: usize = 12;
    const _: [(); 12] = [(); UCRED_BYTES];

    extern "C" {
        fn getsockopt(
            fd: c_int,
            level: c_int,
            option: c_int,
            value: *mut c_void,
            length: *mut u32,
        ) -> c_int;
    }

    pub(super) fn adopt_raw(fd: RawFd, name: &str) -> Result<UnixListener, ActivationError> {
        let target = std::fs::read_link(format!("/proc/self/fd/{fd}")).map_err(|_| {
            ActivationError::NotASocket {
                fd,
                name: name.to_string(),
            }
        })?;
        if !target
            .as_os_str()
            .as_encoded_bytes()
            .starts_with(b"socket:")
        {
            return Err(ActivationError::NotASocket {
                fd,
                name: name.to_string(),
            });
        }

        // SAFETY: production reaches this only through `take`, called first in `main` before
        // any thread exists, after `parse` checked LISTEN_PID, the fd range and the once guard.
        // Tests reach it only with a descriptor they own and release with `into_raw_fd`. A
        // non-socket returns above, so `from_raw_fd` never sees a descriptor the caller still
        // owns; after this line, `owned` is its unique owner.
        let owned = unsafe { OwnedFd::from_raw_fd(fd) };
        if rustix::net::sockopt::socket_domain(&owned) != Ok(AddressFamily::UNIX) {
            return Err(ActivationError::WrongSocket {
                fd,
                name: name.to_string(),
                want: "an AF_UNIX socket",
            });
        }
        if rustix::net::sockopt::socket_type(&owned) != Ok(SocketType::STREAM) {
            return Err(ActivationError::WrongSocket {
                fd,
                name: name.to_string(),
                want: "a SOCK_STREAM socket",
            });
        }
        if rustix::net::sockopt::socket_acceptconn(&owned) != Ok(true) {
            return Err(ActivationError::WrongSocket {
                fd,
                name: name.to_string(),
                want: "a listening socket",
            });
        }
        rustix::io::fcntl_setfd(&owned, FdFlags::CLOEXEC).map_err(|_| {
            ActivationError::FdOperation {
                fd,
                name: name.to_string(),
                operation: "marked close-on-exec",
            }
        })?;
        let listener = UnixListener::from(owned);
        listener
            .set_nonblocking(true)
            .map_err(|_| ActivationError::FdOperation {
                fd,
                name: name.to_string(),
                operation: "made non-blocking",
            })?;
        Ok(listener)
    }

    /// Read the raw Linux `ucred` bytes and the length returned by `SO_PEERCRED`.
    ///
    /// The caller must verify that the length is exactly 12 before decoding the bytes. Keeping
    /// the pid as raw bytes lets the caller handle pid 0 without rustix's `NonZeroI32` conversion.
    pub fn peer_credentials_raw<Fd: AsFd>(fd: Fd) -> io::Result<([u8; UCRED_BYTES], u32)> {
        let mut credentials = [0_u8; UCRED_BYTES];
        let mut length = 12_u32;
        // SAFETY: `fd` is a live borrowed socket descriptor. The `extern "C"` declaration above
        // matches Linux's `getsockopt` ABI (`c_int` arguments/result, a `c_void` value pointer and
        // `u32` `socklen_t`). `credentials` has exactly 12 writable bytes, and `length` points to a
        // live socklen_t initialized to that capacity. Linux's `SO_PEERCRED` writes no more than
        // the supplied capacity and reports the returned length.
        let result = unsafe {
            getsockopt(
                fd.as_fd().as_raw_fd(),
                SOL_SOCKET,
                SO_PEERCRED,
                credentials.as_mut_ptr().cast::<c_void>(),
                &mut length,
            )
        };
        if result == -1 {
            return Err(io::Error::last_os_error());
        }
        Ok((credentials, length))
    }
}

use raw::adopt_raw;
pub use raw::peer_credentials_raw;

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
        for leading_zero in ["041", "00", "01"] {
            assert_eq!(
                parse(Some(leading_zero), Some("1"), Some("observe"), 1),
                Err(ActivationError::InvalidVariable("LISTEN_PID"))
            );
        }
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
        let adopted = adopt_raw(fd, "observe").expect("listening socket is adoptable");
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
            adopt_raw(fd, "operator"),
            Err(ActivationError::WrongSocket {
                fd: got,
                ref name,
                want: "a listening socket",
            }) if got == fd && name == "operator"
        ));
    }

    #[test]
    fn adopt_raw_refuses_a_unix_datagram() {
        let datagram = UnixDatagram::unbound().expect("unbound datagram");
        let fd = datagram.into_raw_fd();
        assert!(matches!(
            adopt_raw(fd, "observer"),
            Err(ActivationError::WrongSocket {
                fd: got,
                ref name,
                want: "a SOCK_STREAM socket",
            }) if got == fd && name == "observer"
        ));
    }

    #[test]
    fn adopt_raw_refuses_a_read_only_file_without_taking_its_ownership() {
        let file = File::open("/dev/null").expect("read-only file");
        let fd = file.as_raw_fd();
        assert!(matches!(
            adopt_raw(fd, "observe"),
            Err(ActivationError::NotASocket {
                fd: got,
                ref name,
            }) if got == fd && name == "observe"
        ));
        file.metadata()
            .expect("the test still owns and can inspect the file");
    }

    #[test]
    fn take_is_one_shot_even_after_a_failed_first_call() {
        let pid = std::process::id();
        let other_pid = pid.wrapping_add(1);
        assert!(matches!(
            take(None, None, None, pid),
            Err(ActivationError::MissingVariable("LISTEN_PID"))
        ));
        assert!(matches!(
            take(
                Some(&other_pid.to_string()),
                Some("1"),
                Some("observe"),
                pid
            ),
            Err(ActivationError::AlreadyTaken)
        ));
    }

    #[test]
    fn every_refusal_renders_one_line_and_names_its_fact_or_socket() {
        let cases = [
            (
                ActivationError::MissingVariable("LISTEN_PID"),
                "LISTEN_PID is required",
            ),
            (
                ActivationError::InvalidVariable("LISTEN_FDS"),
                "LISTEN_FDS must be a decimal integer",
            ),
            (
                ActivationError::PidMismatch {
                    listen_pid: 42,
                    my_pid: 41,
                },
                "LISTEN_PID is 42, but this process is 41",
            ),
            (ActivationError::FdCount(0), "LISTEN_FDS is 0"),
            (
                ActivationError::NameCount { fds: 2, names: 1 },
                "LISTEN_FDNAMES has 1 names",
            ),
            (ActivationError::EmptyName(1), "LISTEN_FDNAMES name 1"),
            (
                ActivationError::DuplicateName(String::from("observe")),
                r#"repeats the name "observe""#,
            ),
            (ActivationError::AlreadyTaken, "LISTEN_FDS has already"),
            (
                ActivationError::NotASocket {
                    fd: 3,
                    name: String::from("ob\nserve"),
                },
                r#""ob\nserve""#,
            ),
            (
                ActivationError::WrongSocket {
                    fd: 3,
                    name: String::from("ob\nserve"),
                    want: "a listening socket",
                },
                "a listening socket",
            ),
            (
                ActivationError::FdOperation {
                    fd: 3,
                    name: String::from("ob\nserve"),
                    operation: "made non-blocking",
                },
                "made non-blocking",
            ),
        ];
        for (error, expected) in cases {
            let text = error.to_string();
            assert!(!text.contains('\n'), "{text:?}");
            assert!(text.contains(expected), "missing {expected:?}: {text}");
        }
    }
}
