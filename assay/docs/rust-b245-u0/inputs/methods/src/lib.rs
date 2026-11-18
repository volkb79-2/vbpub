pub struct S {
    pub n: i32,
}

impl S {
    pub fn bump(&self, a: i32) -> i32 {
        self.n + a
    }
}

pub trait T {
    fn triple(&self) -> i32;
}

impl T for S {
    fn triple(&self) -> i32 {
        self.n * 3
    }
}

pub fn pick<X: Copy>(a: X, _b: X, first: bool) -> X {
    if first {
        a
    } else {
        _b
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn all() {
        let s = S { n: 2 };
        assert_eq!(s.bump(1), 3);
        assert_eq!(s.triple(), 6);
        assert_eq!(pick(1, 2, true), 1);
    }
}
