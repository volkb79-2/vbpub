// Package hello is assay's committed Go fixture (P08, DESIGN-GUIDE §10): a
// hello-world Go source paired with the coverprofile in ./hello.out.
//
// ./hello.out is REAL `go test -coverprofile` output (F008-A4), produced from
// THESE bytes by nyxloom-trove/carve-assets/P27-recarve/regenerate-fixtures.sh
// inside tester-unified-go:local under --network=none. This devcontainer has
// no Go toolchain and must not acquire one (A-042/A-043/A-087), which is why
// the profile is committed rather than generated at test time. It replaces the
// hand-authored bytes A-234 recorded as wrong in BOTH coordinates: the start
// columns were off by two, and the blocks ended at the return statement's own
// column instead of at Rbrace+1 (A-218). The old header's claim that closing
// braces are "untracked by any block" was false of every real profile.
//
// EDITING THIS FILE INVALIDATES ./hello.out. A cover block's extent is a
// line/column pair, so any edit above a function body -- this comment included
// -- moves it. Re-run the script above, then re-derive the expectations from
// the oracle document it also writes. That instruction is enforceable rather
// than advisory: tests/test_adapters_go_union_fidelity.py joins the profile
// against the committed oracle and refuses (UNREADABLE_ARTIFACT) the moment
// the two sets of extents disagree.
//
// The test the profile was generated against exercises Greet only and lives in
// the script, never here -- a committed _test.go would be compiled by nothing
// and would drift silently. Farewell is deliberately never called: it is what
// models the MISSING half.
package hello

import "fmt"

// Greet returns a friendly hello-world greeting. ./hello.out marks its body
// EXECUTED.
func Greet(name string) string {
	return fmt.Sprintf("Hello, %s!", name)
}

// Farewell returns a goodbye message. ./hello.out marks its body MISSING --
// modelling a changed function nobody has exercised yet.
func Farewell(name string) string {
	return fmt.Sprintf("Goodbye, %s!", name)
}
