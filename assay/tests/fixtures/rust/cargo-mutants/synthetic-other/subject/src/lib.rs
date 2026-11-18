//! Synthetic cargo-mutants subject for assay R-3a spikes (no dependencies).

pub struct Token {
    pub id: u32,
}

/// Caught: the tests pin the sum.
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

/// Missed: no test calls it.
pub fn is_even(n: u32) -> bool {
    n % 2 == 0
}

/// Unviable: `Token` has no `Default`, so the FnValue mutant does not compile.
pub fn make(id: u32) -> Token {
    Token { id }
}

/// Timeout: `i += 1` -> `i *= 1` never terminates.
pub fn spin(n: u32) -> u32 {
    let mut i = 0;
    while i < n {
        i += 1;
    }
    i
}

/// Match arms and a guard.
pub fn pick(a: Option<u32>) -> u32 {
    match a {
        Some(v) if v > 10 => v,
        Some(_) => 1,
        None => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn add_sums() {
        assert_eq!(add(2, 3), 5);
    }

    #[test]
    fn make_keeps_id() {
        assert_eq!(make(7).id, 7);
    }

    #[test]
    fn spin_counts() {
        assert_eq!(spin(4), 4);
    }

    #[test]
    fn pick_cases() {
        assert_eq!(pick(Some(20)), 20);
        assert_eq!(pick(None), 0);
    }
}
