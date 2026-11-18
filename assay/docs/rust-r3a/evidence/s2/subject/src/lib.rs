pub fn uni(a: usize) -> usize {
    let s = "héllo→"; s.len() + a
}

pub fn multi(a: u32) -> u32 {
    let b = a + 1;
    b * 2
}

#[cfg(test)]
mod tests {
    #[test]
    fn t() {
        assert_eq!(super::multi(1), 4);
        assert_eq!(super::uni(1), 10);
    }
}
