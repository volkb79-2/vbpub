//! M0 scaffold for hostobs-proto; hostobs behavior is not implemented here.

pub fn package_name() -> &'static str {
    env!("CARGO_PKG_NAME")
}

#[cfg(test)]
mod tests {
    #[test]
    fn package_identity_matches_the_workspace_boundary() {
        assert_eq!(super::package_name(), "hostobs-proto");
    }
}
