#[path = "with space.rs"]
pub mod spaced;
#[path = "módulo.rs"]
pub mod modulo;

#[cfg(test)]
mod tests {
    #[test]
    fn both_work() {
        assert_eq!(super::spaced::inc(1), 2);
        assert_eq!(super::modulo::dbl(2), 4);
    }
}
