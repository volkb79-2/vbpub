//! The hostobs probe's serving side, built as pure functions first (design
//! `docs/P5B-SERVER-DESIGN.md`, package P5b-1a).
//!
//! Each module owns one concern and none of them performs I/O, reads a clock or keeps
//! connection state: [`limits`] (levels and the section 5.1 bounds), [`verbs`] (the section 4.3
//! table), [`codes`] (the wire error codes and `detail`), [`args`] (the strict request-argument
//! reader and the `hello` and `status` schemas), [`classify`] (the request precedence function,
//! design R11), [`encode`] (the control frames, error envelope and JSON lines) and [`lines`]
//! (the JSON line reader). Package P5b-1b adds [`backend`] (the injected identity and status
//! source), [`conn`] (the sans-IO connection state machine) and [`server`] (the `Server` that
//! owns the one send hub, the connections and the per-level counters); the drivers arrive in
//! later packages. P5b-2a adds [`selection`], the checked canonical stream key. P5b-2b adds
//! [`tick`] (backend tick validation and encoding) and [`registry`] (the shared selection
//! streams).

#![warn(missing_docs)]

pub mod args;
pub mod backend;
pub mod classify;
pub mod codes;
pub mod conn;
pub mod encode;
pub mod limits;
pub mod lines;
pub mod registry;
pub mod selection;
pub mod server;
pub mod tick;
pub mod verbs;

/// The package name, `hostobs-probe` (the M0 scaffold identity).
pub fn package_name() -> &'static str {
    env!("CARGO_PKG_NAME")
}

#[cfg(test)]
mod tests {
    #[test]
    fn package_identity_matches_the_workspace_boundary() {
        assert_eq!(super::package_name(), "hostobs-probe");
    }
}
