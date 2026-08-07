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
