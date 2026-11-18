//! The hostobs probe's serving side, built as pure functions first (design
//! `docs/P5B-SERVER-DESIGN.md`, package P5b-1a).
//!
//! Each module owns one concern and none of them performs I/O, reads a clock or keeps
//! connection state: [`limits`] (levels and the section 5.1 bounds), [`verbs`] (the section 4.3
//! table), [`codes`] (the wire error codes and `detail`), [`args`] (the strict request-argument
//! reader and the `hello` and `status` schemas). The connection state machine and the drivers
//! arrive in later packages.

#![warn(missing_docs)]

pub mod args;
pub mod codes;
pub mod encode;
pub mod limits;
pub mod lines;
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
