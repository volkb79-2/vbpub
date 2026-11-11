fn main() {
    let _ = hostobs_act::package_name();
}

#[cfg(test)]
mod tests {
    #[test]
    fn binary_entry_uses_the_act_library_boundary() {
        assert_eq!(hostobs_act::package_name(), "hostobs-act");
        super::main();
    }
}
