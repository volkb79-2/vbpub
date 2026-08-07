// Package greet is assay's committed Go canary fixture (P09, A-105/A-107).
// This models the KNOWN-GOOD control half of the cause-sensitive canary: a
// real Go source file whose only function is fully exercised, paired with
// the PRE-GENERATED coverprofile in greet_control.out (committed as literal
// text -- this devcontainer has no Go toolchain, DESIGN-GUIDE Sec.10/A-042).
//
// greet_transformed.out pairs with GoAdapter().inject_uncovered_line's own
// PURE transform of THIS file's text, computed fresh at test time (never
// committed as a second .go file, so the fixture cannot drift from the real
// adapter under test): the appended never-called function's two body
// statements are marked MISSING there.
package greet

import "fmt"

// Greet returns a friendly greeting. greet_control.out marks this body
// EXECUTED.
func Greet(name string) string {
	return fmt.Sprintf("Hello, %s!", name)
}
