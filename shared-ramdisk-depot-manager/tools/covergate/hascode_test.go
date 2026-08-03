package main

import (
	"os"
	"path/filepath"
	"testing"
)

func writeGo(t *testing.T, dir, name, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// The motivating case: srdm's package skeleton ships doc.go files that are
// nothing but a package clause and a long comment. They produce no cover
// blocks, so an earlier version of this gate reported all 94 of their lines
// as uncovered — a verdict no test could ever clear.
func TestHasExecutableCodeIsFalseForACommentOnlyFile(t *testing.T) {
	dir := t.TempDir()
	writeGo(t, dir, "doc.go", `// Package expose will own the exposure drivers.
//
// Empty until P03.
package expose
`)
	if HasExecutableCode(dir, "doc.go") {
		t.Fatal("a comment-only doc.go was reported as having executable code")
	}
}

func TestHasExecutableCodeIsFalseForDeclarationsOnly(t *testing.T) {
	dir := t.TempDir()
	writeGo(t, dir, "types.go", `package x

import "errors"

type Kind string

const (
	KindA Kind = "a"
	KindB Kind = "b"
)

var ErrNope = errors.New("nope")
`)
	// go test -cover instruments function bodies and nothing else, so types,
	// constants and package-level vars yield no blocks.
	if HasExecutableCode(dir, "types.go") {
		t.Fatal("a declarations-only file was reported as having executable code")
	}
}

func TestHasExecutableCodeIsTrueForAnyFunctionBody(t *testing.T) {
	dir := t.TempDir()
	writeGo(t, dir, "f.go", `package x

func Add(a, b int) int { return a + b }
`)
	if !HasExecutableCode(dir, "f.go") {
		t.Fatal("a file with a function was reported as having no executable code")
	}
}

func TestHasExecutableCodeIsFalseForBodilessDeclarations(t *testing.T) {
	dir := t.TempDir()
	// An assembly or cgo stub has no Go statements to instrument.
	writeGo(t, dir, "asm.go", `package x

func fastPath(x uint64) uint64
`)
	if HasExecutableCode(dir, "asm.go") {
		t.Fatal("a bodiless declaration was treated as instrumentable")
	}
}

// A file the gate cannot read or parse must fail CLOSED. Excusing it from
// coverage would let a syntax error buy an exemption.
func TestHasExecutableCodeFailsClosedOnUnreadableOrUnparsableFiles(t *testing.T) {
	dir := t.TempDir()
	if HasExecutableCode(dir, "absent.go") {
		// true is the strict reading: treat it as code and let the rest of
		// the gate report it as unmeasured.
	} else {
		t.Error("a missing file was excused from coverage")
	}

	writeGo(t, dir, "broken.go", "package x\n\nfunc (((\n")
	if !HasExecutableCode(dir, "broken.go") {
		t.Error("an unparsable file was excused from coverage")
	}
}

func TestEvaluateExcludesFilesWithNoExecutableCode(t *testing.T) {
	added := map[string]map[int]bool{
		"internal/expose/doc.go": lines(1, 2, 3, 4),
		"internal/real.go":       lines(10),
	}
	coverage := map[string]*FileCoverage{
		"internal/real.go": cov([]int{10}, nil),
	}
	hasCode := func(p string) bool { return p != "internal/expose/doc.go" }

	v := Evaluate(added, coverage, "internal", ".go", 100, isGoTestFile, hasCode)

	if v.ChangedExecutable != 1 {
		t.Fatalf("ChangedExecutable = %d, want 1; the doc.go has no blocks to cover",
			v.ChangedExecutable)
	}
	if len(v.Unmeasured) != 0 {
		t.Errorf("a code-free file was reported unmeasured: %v", v.Unmeasured)
	}
	if len(v.NoCode) != 1 || v.NoCode[0] != "internal/expose/doc.go" {
		t.Errorf("NoCode = %v, want the doc.go", v.NoCode)
	}
	if !v.Passed() {
		t.Error("a change adding only a doc.go failed a coverage gate")
	}
}

// The exemption must not extend to a file that DOES have functions and is
// simply absent from the profile — that is a real problem.
func TestEvaluateStillFlagsAbsentFilesThatHaveCode(t *testing.T) {
	added := map[string]map[int]bool{"internal/new.go": lines(5, 6)}
	hasCode := func(string) bool { return true }

	v := Evaluate(added, map[string]*FileCoverage{}, "internal", ".go", 100, isGoTestFile, hasCode)

	if len(v.Unmeasured) != 1 {
		t.Fatalf("Unmeasured = %v, want the absent file", v.Unmeasured)
	}
	if v.Passed() {
		t.Error("a source file absent from the profile passed the gate")
	}
}
