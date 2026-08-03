package main

import (
	"strings"
	"testing"
)

func TestParseCoverProfileClassifiesLinesThreeWays(t *testing.T) {
	profile := `mode: atomic
srdm/internal/store/store.go:10.20,14.3 3 1
srdm/internal/store/store.go:20.2,22.9 2 0
`
	got, err := ParseCoverProfile(strings.NewReader(profile))
	if err != nil {
		t.Fatal(err)
	}
	fc := got["srdm/internal/store/store.go"]
	if fc == nil {
		t.Fatal("the file is missing from the parsed profile")
	}

	for l := 10; l <= 14; l++ {
		if !fc.Executed[l] {
			t.Errorf("line %d should be executed", l)
		}
	}
	for l := 20; l <= 22; l++ {
		if !fc.Missing[l] {
			t.Errorf("line %d should be missing", l)
		}
	}
	// A line in no block is not code. This is the discriminator that stops
	// a comment edit from reading as an uncovered-code event.
	for _, l := range []int{1, 9, 15, 19, 23, 100} {
		if fc.Executable(l) {
			t.Errorf("line %d is in no block and must not count as executable", l)
		}
	}
}

// Go blocks overlap: `} else {` closes one and opens another. A line the
// code demonstrably ran must not be reported uncovered because a
// never-taken block also spans it.
func TestParseCoverProfileExecutedWinsOverMissingOnOverlap(t *testing.T) {
	t.Run("missing block first", func(t *testing.T) {
		got, err := ParseCoverProfile(strings.NewReader(
			"mode: set\nf.go:10.1,12.1 1 0\nf.go:12.1,14.1 1 1\n"))
		if err != nil {
			t.Fatal(err)
		}
		fc := got["f.go"]
		if !fc.Executed[12] || fc.Missing[12] {
			t.Fatalf("line 12: executed=%v missing=%v; executed must win",
				fc.Executed[12], fc.Missing[12])
		}
	})

	t.Run("executed block first", func(t *testing.T) {
		got, err := ParseCoverProfile(strings.NewReader(
			"mode: set\nf.go:10.1,12.1 1 1\nf.go:12.1,14.1 1 0\n"))
		if err != nil {
			t.Fatal(err)
		}
		fc := got["f.go"]
		if !fc.Executed[12] || fc.Missing[12] {
			t.Fatalf("line 12: executed=%v missing=%v; a later never-taken block "+
				"must not demote an executed line", fc.Executed[12], fc.Missing[12])
		}
	})
}

func TestParseCoverProfileHandlesColonsInPaths(t *testing.T) {
	got, err := ParseCoverProfile(strings.NewReader(
		"mode: set\nweird:name/f.go:1.1,2.1 1 1\n"))
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := got["weird:name/f.go"]; !ok {
		t.Fatalf("path with a colon parsed as %v; the position is delimited by the LAST colon",
			keysOf(got))
	}
}

func TestParseCoverProfileRejectsMalformedRecords(t *testing.T) {
	cases := map[string]string{
		"too few fields":  "mode: set\nf.go:1.1,2.1 1\n",
		"no position":     "mode: set\nfgo 1 1\n",
		"no range":        "mode: set\nf.go:1.1 1 1\n",
		"bad line number": "mode: set\nf.go:x.1,2.1 1 1\n",
		"zero line":       "mode: set\nf.go:0.1,2.1 1 1\n",
		"reversed range":  "mode: set\nf.go:5.1,2.1 1 1\n",
		"bad count":       "mode: set\nf.go:1.1,2.1 1 many\n",
		"negative count":  "mode: set\nf.go:1.1,2.1 1 -1\n",
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			// Silently skipping a record it cannot read would make the gate
			// under-report coverage, which fails closed — but for a reason
			// nobody could diagnose from the output.
			if _, err := ParseCoverProfile(strings.NewReader(body)); err == nil {
				t.Fatal("a malformed record was accepted")
			}
		})
	}
}

func TestParseCoverProfileIgnoresBlanksAndMode(t *testing.T) {
	got, err := ParseCoverProfile(strings.NewReader("mode: atomic\n\n\nf.go:1.1,2.1 1 1\n\n"))
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("parsed %d files, want 1", len(got))
	}
}

func TestStripModulePrefix(t *testing.T) {
	in := map[string]*FileCoverage{
		"srdm/internal/store/store.go": {},
		"srdm/cmd/srdm/main.go":        {},
		"other/pkg/x.go":               {},
	}
	got := stripModulePrefix(in, "srdm")

	for _, want := range []string{"internal/store/store.go", "cmd/srdm/main.go"} {
		if _, ok := got[want]; !ok {
			t.Errorf("missing %q; got %v", want, keysOf(got))
		}
	}
	// A path that is not under the module keeps its spelling rather than
	// being mangled into a false match.
	if _, ok := got["other/pkg/x.go"]; !ok {
		t.Errorf("a non-module path was rewritten; got %v", keysOf(got))
	}
}

func keysOf[T any](m map[string]T) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
