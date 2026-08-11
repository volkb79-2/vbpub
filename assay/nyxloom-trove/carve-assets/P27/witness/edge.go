package app

// ColOne is deliberately NOT gofmt-formatted. The bare block's opening brace
// sits at column 1 of line 7, so the function-body prefix block that ends AT
// that following token reports an end column of exactly 1.
func ColOne(n int) int {
{
n++
}
return n
}
