#[cfg_attr(not(test), allow(dead_code))]
mod config;

fn main() {
    let _ = hostobs_probe::package_name();
}

#[cfg(test)]
mod tests {
    #[test]
    fn binary_entry_returns_without_host_side_effects() {
        assert_eq!(hostobs_probe::package_name(), "hostobs-probe");
        super::main();
    }
}
