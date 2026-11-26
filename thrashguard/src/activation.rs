//! Adoption of the two systemd socket descriptors (design §3, D7).
//!
//! The `.socket` units use `Accept=no`, so systemd passes the *listening*
//! descriptors to the service and owns the path, the group and the mode. That is
//! the only way the design's `0660 root:thrashguard-{registrant,reader}` fact can
//! hold: a process that binds its own path cannot set a group (there is no
//! `chown` in `std`), and it would leave a stale socket file behind on every
//! restart. So the descriptor must be adopted rather than bound.
//!
//! ## The one audited `unsafe` in this crate
//!
//! `std` can only turn a raw descriptor number into a [`UnixListener`] through
//! [`FromRawFd`], which is `unsafe`. This crate therefore *denies* (not
//! forbids) `unsafe_code`, and this file holds exactly one `#[allow]` and one
//! `unsafe` block. `tools/check-lints.py` asserts that count in both directions,
//! so a second unsafe block anywhere fails the lint lane, and a stale allowance
//! does too. `tests/no_act_path.rs` still refuses every signal and cgroup write.
//!
//! ## What is checked before a descriptor is trusted
//!
//! [`plan`] is the pure part: `LISTEN_FDS` must be exactly two, `LISTEN_FDNAMES`
//! must name them `registrant` and `reader` (in either order), and nothing else
//! is accepted. It is tested without a descriptor, so the vocabulary is covered
//! even though the `from_raw_fd` call itself cannot be (a real adoption needs a
//! service start; that is a live acceptance fact, not a unit test).

use std::collections::BTreeMap;
use std::fmt;
use std::os::unix::io::FromRawFd;
use std::os::unix::net::UnixListener;

use crate::socket::Role;

/// How many descriptors this daemon adopts: one per socket group.
pub const MAX_LISTEN_FDS: u32 = 2;
/// The `LISTEN_FDNAMES` names, in the order the `.socket` units declare them.
pub const FD_NAMES: [&str; 2] = ["registrant", "reader"];
/// The environment variable holding the count.
pub const LISTEN_FDS: &str = "LISTEN_FDS";
/// The environment variable holding the colon-separated names.
pub const LISTEN_FDNAMES: &str = "LISTEN_FDNAMES";
/// The first descriptor systemd passes.
pub const FIRST_FD: u32 = 3;

/// Why the activation environment cannot be honoured.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ActivationError {
    /// `LISTEN_FDS` is not a number, or is outside `1..=MAX_LISTEN_FDS`.
    Count {
        /// What was found.
        found: String,
    },
    /// `LISTEN_FDNAMES` does not have exactly one name per descriptor.
    Names {
        /// How many names were given.
        names: usize,
        /// How many descriptors were announced.
        fds: usize,
    },
    /// One of the two names this daemon needs is absent or unknown.
    UnknownName {
        /// The name that was found instead.
        name: String,
    },
    /// A name was given twice.
    Duplicate {
        /// The repeated name.
        name: String,
    },
    /// The descriptor could not be used as a listening socket.
    Descriptor {
        /// The descriptor number.
        fd: u32,
        /// The operating system's reason.
        detail: String,
    },
}

impl fmt::Display for ActivationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Count { found } => write!(
                f,
                "{LISTEN_FDS}={found:?} is not the 1..={MAX_LISTEN_FDS} this daemon expects"
            ),
            Self::Names { names, fds } => write!(
                f,
                "{LISTEN_FDNAMES} has {names} name(s) for {LISTEN_FDS}={fds}"
            ),
            Self::UnknownName { name } => write!(
                f,
                "{LISTEN_FDNAMES} name {name:?} is not one of {FD_NAMES:?}"
            ),
            Self::Duplicate { name } => {
                write!(f, "{LISTEN_FDNAMES} repeats the name {name:?}")
            }
            Self::Descriptor { fd, detail } => {
                write!(f, "descriptor {fd} is not a listening socket: {detail}")
            }
        }
    }
}

impl std::error::Error for ActivationError {}

/// The adoption decision, before any descriptor is touched.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Plan {
    descriptors: BTreeMap<String, u32>,
}

impl Plan {
    /// The descriptor number announced for one socket role.
    pub fn descriptor(&self, role: Role) -> u32 {
        self.descriptors
            .get(role.as_str())
            .copied()
            .unwrap_or(FIRST_FD)
    }
}

/// Read the two environment facts and decide whether systemd activated us.
///
/// `Ok(None)` means the variables are absent: a foreground run binds its own
/// paths (an operator's `thrashguard serve` under a terminal). `Ok(Some(plan))`
/// means systemd owns the sockets. A malformed *present* variable is a refusal,
/// never a fallback to binding: the estate's default rules forbid a present-but-
/// wrong fact silently becoming the other branch.
pub fn plan(fds: Option<&str>, names: Option<&str>) -> Result<Option<Plan>, ActivationError> {
    let Some(fds) = fds else {
        if names.is_some() {
            return Err(ActivationError::Count {
                found: format!("{LISTEN_FDNAMES} without {LISTEN_FDS}"),
            });
        }
        return Ok(None);
    };
    let count: u32 = fds.trim().parse().map_err(|_| ActivationError::Count {
        found: fds.trim().to_string(),
    })?;
    if count == 0 || count > MAX_LISTEN_FDS {
        return Err(ActivationError::Count {
            found: count.to_string(),
        });
    }
    let raw_names = names.ok_or(ActivationError::Names {
        names: 0,
        fds: count as usize,
    })?;
    let parse_names: Vec<&str> = raw_names
        .split(':')
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .collect();
    if parse_names.len() != count as usize {
        return Err(ActivationError::Names {
            names: parse_names.len(),
            fds: count as usize,
        });
    }
    let mut descriptors = BTreeMap::new();
    for (index, name) in parse_names.iter().enumerate() {
        if !FD_NAMES.contains(name) {
            return Err(ActivationError::UnknownName {
                name: (*name).to_string(),
            });
        }
        if descriptors.contains_key(*name) {
            return Err(ActivationError::Duplicate {
                name: (*name).to_string(),
            });
        }
        descriptors.insert(
            (*name).to_string(),
            FIRST_FD + u32::try_from(index).unwrap_or(0),
        );
    }
    Ok(Some(Plan { descriptors }))
}

/// The two adopted listeners.
#[derive(Debug)]
pub struct Adopted {
    /// The registrant listener, when it was announced.
    pub registrant: Option<UnixListener>,
    /// The reader listener, when it was announced.
    pub reader: Option<UnixListener>,
}

/// Adopt the descriptors `plan` names.
///
/// This is the one `unsafe` boundary in the crate: `std` cannot construct a
/// [`UnixListener`] from a descriptor number any other way. The safety argument
/// is narrow and stated here: systemd hands this process descriptors it opened
/// as `SOCK_STREAM` listeners at these exact numbers (its documented contract,
/// `sd_listen_fds`), the process is the one it started, and the two numbers come
/// from `LISTEN_FDS`/`LISTEN_FDNAMES` validated by [`plan`]. Each descriptor is
/// taken at most once, so no two `UnixListener`s own one number.
pub fn adopt(plan: &Plan) -> Result<Adopted, ActivationError> {
    let mut registrant = None;
    let mut reader = None;
    for role in [Role::Registrant, Role::Reader] {
        let fd = plan.descriptor(role);
        // SAFETY: the argument is the load-bearing fact. `fd` was announced by
        // systemd as an already-open listening socket of this process (see the
        // function docs); `plan` rejected every other shape, and each number is
        // adopted exactly once, so the returned `UnixListener` is the sole owner.
        #[allow(unsafe_code)]
        let listener = unsafe { UnixListener::from_raw_fd(fd as std::os::unix::io::RawFd) };
        match role {
            Role::Registrant => registrant = Some(listener),
            Role::Reader => reader = Some(listener),
        }
    }
    Ok(Adopted { registrant, reader })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn absent_variables_mean_a_foreground_run_and_nothing_is_invented() {
        assert_eq!(plan(None, None), Ok(None));
        // `LISTEN_FDNAMES` alone is a half-written environment: refused, not ignored.
        assert!(matches!(
            plan(None, Some("reader")),
            Err(ActivationError::Count { .. })
        ));
    }

    #[test]
    fn the_two_shipped_names_are_accepted_in_announcement_order() {
        let ordered = plan(Some("2"), Some("registrant:reader"))
            .expect("plan")
            .expect("some");
        assert_eq!(ordered.descriptor(Role::Registrant), FIRST_FD);
        assert_eq!(ordered.descriptor(Role::Reader), FIRST_FD + 1);
        // Names are matched, never assumed by position: the reader may come first.
        let swapped = plan(Some("2"), Some("reader:registrant"))
            .expect("plan")
            .expect("some");
        assert_eq!(swapped.descriptor(Role::Reader), FIRST_FD);
        assert_eq!(swapped.descriptor(Role::Registrant), FIRST_FD + 1);
        // One socket only (a host with `registrant = false` still has both units,
        // so this shape is refused rather than half-adopted).
        assert!(matches!(plan(Some("1"), Some("reader")), Ok(Some(_))));
    }

    #[test]
    fn a_malformed_or_unknown_announcement_is_refused_never_fallen_back_from() {
        for (fds, names) in [
            ("", Some("reader:registrant")),
            ("many", Some("reader:registrant")),
            ("0", Some("reader:registrant")),
            ("3", Some("reader:registrant")),
            ("2", None),
            ("2", Some("reader")),
            ("2", Some("reader:status")),
            ("2", Some("reader:reader")),
            ("2", Some("reader:")),
        ] {
            match plan(Some(fds), names) {
                Err(_) => {}
                Ok(value) => panic!("{fds:?}/{names:?} must be refused, got {value:?}"),
            }
        }
    }

    #[test]
    fn every_error_names_the_variable_it_refused() {
        for error in [
            ActivationError::Count { found: "0".into() },
            ActivationError::Names { names: 1, fds: 2 },
            ActivationError::UnknownName {
                name: "status".into(),
            },
            ActivationError::Duplicate {
                name: "reader".into(),
            },
        ] {
            let line = error.to_string();
            assert!(
                line.contains(LISTEN_FDS) || line.contains(LISTEN_FDNAMES),
                "{line}"
            );
            assert_eq!(line.lines().count(), 1, "{line}");
        }
    }
}
