pub fn uni(a: usize) -> usize {
    let s = "héllo→"; s.len() + a
}

#[cfg(test)]
mod tests {
    #[test]
    fn t() {
        assert_eq!(super::uni(1), 10);
    }
}
