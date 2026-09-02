// Package greet is assay's committed Go canary fixture (P09, A-105/A-107).
// This models the KNOWN-GOOD control half of the cause-sensitive canary: a
// real Go source file whose only function is fully exercised, paired with the
// coverprofile in greet_control.out.
//
// BOTH committed profiles here are REAL `go test -coverprofile` output
// (F008-A4), produced from THESE bytes by
// nyxloom-trove/carve-assets/P27-recarve/regenerate-fixtures.sh inside
// tester-unified-go:local under --network=none. They are committed rather than
// generated at test time because this devcontainer has no Go toolchain
// (A-042/A-087, DESIGN-GUIDE Sec.10). They replace the hand-authored pair
// A-234 records as wrong in both coordinates.
//
// greet_transformed.out is the profile of GoAdapter().inject_uncovered_line's
// own PURE transform of THIS file's text. The transformed source is computed
// by that script from the real adapter and is never committed as a second .go
// file, so the fixture cannot drift from the adapter under test; the appended
// never-called function's two body statements are MISSING there.
//
// EDITING THIS FILE INVALIDATES BOTH PROFILES -- a cover block's extent is a
// line/column pair. Re-run the script, which also rewrites the oracle document
// tests/test_canary_go_pipeline.py joins each profile against; that join
// refuses loudly the moment the two sets of extents disagree.
package greet

import "fmt"

// Greet returns a friendly greeting. greet_control.out marks this body
// EXECUTED.
func Greet(name string) string {
	return fmt.Sprintf("Hello, %s!", name)
}
