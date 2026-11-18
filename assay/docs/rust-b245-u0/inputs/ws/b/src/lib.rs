pub fn quad(x: i32) -> i32 {
    a::double(a::double(x))
}

#[cfg(test)]
mod tests {
    #[test]
    fn quad_is_four_times() {
        assert_eq!(super::quad(3), 12);
    }
}
