//! thrashguard — the `observe` cut (TG-1, design §5 row P1).
//!
//! This crate contains **no act path**: it reads pressure and cgroupfs, it
//! decides what it *would* freeze, and it journals that decision. Nothing here
//! writes `cgroup.freeze`, `memory.reclaim` or any other cgroup file, and
//! nothing sends a signal; `tests/no_act_path.rs` asserts that mechanically.
//!
//! The one hard boundary this crate keeps (design §3, D7/D12): a registrant
//! names a **container**, never a cgroup path, and the guard resolves the scope
//! itself under `allowed_ancestors` and refuses what it cannot resolve.
//!
//! Module map. Design §5 names the whole set: `psi`, `config`, `socket`,
//! `units`, `victims`, `actuator`, `clock`, `events`, `cli`, plus the wire and
//! report helpers this crate adds. **Landed so far**: [`clock`], [`config`],
//! [`psi`], [`hysteresis`], [`json`], [`units`], [`wire`], [`socket`],
//! [`events`]. The remaining modules (`report`, `cli`, `daemon`, `activation`)
//! are the next subtasks and are deliberately not declared here before they
//! exist: a doc link to a missing module is a rustdoc warning, and the lint lane
//! denies warnings.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

pub mod clock;
pub mod config;
pub mod events;
pub mod hysteresis;
pub mod json;
pub mod psi;
pub mod socket;
pub mod units;
pub mod wire;

#[cfg(test)]
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
