pub fn increment(value: u8) -> u8 {
    value.wrapping_add(1)
}

#[cfg(test)]
mod integration {
    use super::increment;

    #[test]
    fn forced_failure() {
        assert!(
            std::env::var_os("ASSAY_RUST_FIXTURE_FORCE_FAIL").is_none(),
            "the qualification lane requested a real test failure"
        );
        assert_eq!(increment(1), 2);
    }

    #[test]
    fn passing_control() {
        assert_eq!(increment(1), 2);
    }
}
