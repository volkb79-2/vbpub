//! The hostobs probe's process-side library.
//!
//! The sans-IO serving core lives in [`hostobs_serve`]; [`sampler`] owns the real producer
//! and [`live`] is the production backend over its tick.

#![warn(missing_docs)]

pub mod live;
pub mod sampler;

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
