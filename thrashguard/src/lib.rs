//! thrashguard — the `observe` cut (TG-1, design §5 row P1).
//!
//! This crate implements the observe cut: it reads pressure and cgroupfs, decides
//! what it *would* freeze, and journals that decision. The lexical guard in
//! `tests/no_act_path.rs` and `tools/check-lints.py` checks known cgroup-control,
//! process/signal and file-writer patterns. It is not a Rust parser or proof;
//! aliases, macro expansion, generated code, indirect APIs, FFI and unlisted
//! spellings can evade those checks.
//!
//! The one hard boundary this crate keeps (design §3, D7/D12): a registrant
//! names a **container**, never a cgroup path, and the guard resolves the scope
//! itself under `allowed_ancestors` and refuses what it cannot resolve.
//!
//! Module map. Design §5 names the whole set: `psi`, `config`, `socket`,
//! `units`, `victims`, `actuator`, `clock`, `events`, `cli`, plus the wire and
//! report helpers this crate adds. **P1 `observe` lands**: [`clock`], [`config`],
//! [`psi`], [`hysteresis`], [`json`], [`units`], [`wire`], [`socket`], [`events`],
//! [`report`], [`daemon`], [`activation`] and [`cli`]. `victims` (the refault and
//! `smallest-enough` rules) and `actuator` (the act path) are **P2/P3 and do not
//! exist here on purpose**: a doc link to a module that is deliberately absent
//! would be a warning, and a module that half-exists would be worse.
//!
//! `unsafe_code` is **denied**, not forbidden: [`activation`] needs exactly one
//! `unsafe` block to adopt the systemd socket descriptors (`std` has no safe way
//! to wrap a raw fd), and `tools/check-lints.py` asserts that there is exactly
//! one, in that one file.

#![deny(unsafe_code)]
#![warn(missing_docs)]

pub mod activation;
pub mod cli;
pub mod clock;
pub mod config;
pub mod daemon;
pub mod events;
pub mod hysteresis;
pub mod json;
pub mod psi;
pub mod report;
pub mod socket;
pub mod units;
pub mod wire;

#[cfg(test)]
#[doc(hidden)]
pub mod testutil;

/// The wire vocabulary version of a registration request and reply.
pub const REGISTRATION_VERSION: &str = "thrashguard-registration/1";
/// The wire vocabulary version of a journal event and a reader event row.
pub const EVENT_VERSION: &str = "thrashguard-event/1";
/// The wire vocabulary version of a `status` reply.
pub const STATE_VERSION: &str = "thrashguard-state/1";
/// The package version reported by `--version` and the `version` reader verb.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// The one reason string a refused `mode = enforce` carries (design §5: P1
/// compiles no act path, so `enforce` cannot be honoured yet).
pub const ENFORCE_REFUSAL: &str =
    "mode=enforce is not available in this build: thrashguard P1 (`observe`) compiles no act path";
