// STALE, TRACKED (A-234): the committed ./hello.out below does NOT match what
// a real toolchain emits. Real go1.25.12 output is
//   hello/hello.go:29.32,31.2 1 1   /   hello/hello.go:36.35,38.2 1 0
// -- blocks end at Rbrace+1 (A-218), not at the return statement's column, and
// the committed start columns are wrong by two. The claim further down that
// closing braces are "untracked by any block" is FALSE of real profiles.
// Regenerating alone is not an improvement: the correct expectation depends on
// the statement-position oracle A-217 ruled for, which P27's re-carve owns. Full
// evidence and the real bytes: nyxloom-trove/carve-assets/P27/GO-FIXTURES-STALE.md
// and witness/coverage-hello-fixture-REAL.out. Do NOT cite this fixture as proof
// of Go union fidelity until the re-carve lands.
//
// Package hello is assay's committed Go fixture (P08, DESIGN-GUIDE §10): a
// hello-world Go source paired with the PRE-GENERATED coverprofile in
// ./hello.out, committed as literal text because this devcontainer has no
// Go toolchain (A-042/A-087). Regenerate on a host that has Go with:
//
//	cd tests/fixtures/go/hello && go mod init hello
//	cat > hello_test.go <<'EOF'
//	package hello
//
//	import "testing"
//
//	func TestGreet(t *testing.T) {
//		if Greet("world") == "" {
//			t.Fatal("Greet returned empty")
//		}
//	}
//	EOF
//	go test -coverprofile=hello.out .
//	rm hello_test.go go.mod
//
// (Farewell is deliberately never called by that test -- it is what models
// the MISSING lines in the committed hello.out below.)
package hello

import "fmt"

// Greet returns a friendly hello-world greeting. The pre-generated profile
// below marks its body EXECUTED.
func Greet(name string) string {
	return fmt.Sprintf("Hello, %s!", name)
}

// Farewell returns a goodbye message. The pre-generated profile below
// marks its body MISSING -- modelling a changed function nobody has
// exercised yet.
func Farewell(name string) string {
	return fmt.Sprintf("Goodbye, %s!", name)
}
