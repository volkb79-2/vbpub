package app

// Switchy is gofmt-clean: the switch closing brace sits at column 2.
func Switchy(n int) string {
	switch n {
	case 1:
		return "one"
	case 2:
		return "two"
	}
	return "many"
}

// Flat is deliberately NOT gofmt-formatted, so the switch closing brace sits
// at column 1. If a case-clause block's end is the un-extended position of
// the following token, this is where an end column of 1 must appear.
func Flat(n int) string {
switch n {
case 1:
return "one"
case 2:
return "two"
}
return "many"
}

// Bare has a nested bare block whose closing brace is at column 2.
func Bare(n int) int {
	{
		n++
	}
	return n
}

// MultiCond spreads a condition across physical lines.
func MultiCond(a int, b int) bool {
	if a > 0 &&
		b > 0 {
		return true
	}
	return false
}
