package app

// Add is covered, and its body is a single statement on one line.
func Add(a, b int) int {
	return a + b
}

// Classify is covered on exactly one branch: the test passes 11, so the
// if-body executes and the trailing return never does. Two blocks in one
// function, disagreeing about their own lines and nothing else.
func Classify(n int) string {
	if n > 10 {
		return "big"
	}
	return "small"
}

// Sum spans a single statement across three physical lines. The two
// continuation lines belong to that statement; they are not statements of
// their own, and a line-range expansion cannot tell the difference.
func Sum(a, b, c int) int {
	total := a +
		b +
		c
	return total
}

// Unused is never called by calc_test.go, so its whole body is missing.
func Unused(n int) int {
	return n * 2
}
