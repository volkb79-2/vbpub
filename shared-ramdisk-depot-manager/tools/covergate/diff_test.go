package main

import (
	"reflect"
	"testing"
)

func TestParseAddedLinesWalksTheNewSide(t *testing.T) {
	diff := `diff --git a/internal/store/store.go b/internal/store/store.go
index 1111111..2222222 100644
--- a/internal/store/store.go
+++ b/internal/store/store.go
@@ -10,0 +11,3 @@ func Promote() {
+	a := 1
+	b := 2
+	return a + b
@@ -40,2 +44,1 @@ func Old() {
-	removed one
-	removed two
+	replacement
`
	got := ParseAddedLines(diff)
	want := map[string]map[int]bool{
		"internal/store/store.go": {11: true, 12: true, 13: true, 44: true},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ParseAddedLines = %v, want %v", got, want)
	}
}

// A pure deletion advances nothing on the new side. Holding a change
// responsible for covering a line it deleted would be incoherent.
func TestParseAddedLinesIgnoresPureDeletions(t *testing.T) {
	diff := `--- a/internal/x.go
+++ b/internal/x.go
@@ -5,3 +5,0 @@
-	gone one
-	gone two
-	gone three
`
	if got := ParseAddedLines(diff); len(got) != 0 {
		t.Fatalf("a deletion-only diff produced added lines: %v", got)
	}
}

func TestParseAddedLinesIgnoresDeletedFiles(t *testing.T) {
	diff := `--- a/internal/gone.go
+++ /dev/null
@@ -1,2 +0,0 @@
-	package x
-	// bye
`
	if got := ParseAddedLines(diff); len(got) != 0 {
		t.Fatalf("a deleted file contributed added lines: %v", got)
	}
}

func TestParseAddedLinesHandlesHunksWithoutACount(t *testing.T) {
	// `@@ -a +c @@` — the count is omitted when it is 1.
	diff := "--- a/internal/x.go\n+++ b/internal/x.go\n@@ -7 +7 @@\n+	one line\n"
	got := ParseAddedLines(diff)
	want := map[string]map[int]bool{"internal/x.go": {7: true}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ParseAddedLines = %v, want %v", got, want)
	}
}

// The gate runs at -U0, but it must not silently mis-number if someone runs
// it with context lines.
func TestParseAddedLinesAdvancesOverContextLines(t *testing.T) {
	diff := `--- a/internal/x.go
+++ b/internal/x.go
@@ -10,3 +10,4 @@
 	context before
+	added here
 	context after
`
	got := ParseAddedLines(diff)
	want := map[string]map[int]bool{"internal/x.go": {11: true}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ParseAddedLines = %v, want %v", got, want)
	}
}

func TestParseAddedLinesHandlesMultipleFiles(t *testing.T) {
	diff := `--- a/internal/a.go
+++ b/internal/a.go
@@ -1,0 +2,1 @@
+	first
--- a/internal/b.go
+++ b/internal/b.go
@@ -1,0 +9,1 @@
+	second
`
	got := ParseAddedLines(diff)
	want := map[string]map[int]bool{
		"internal/a.go": {2: true},
		"internal/b.go": {9: true},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ParseAddedLines = %v, want %v", got, want)
	}
}

func TestParseAddedLinesToleratesNoPrefixDiffs(t *testing.T) {
	diff := "--- internal/x.go\n+++ internal/x.go\n@@ -1,0 +3,1 @@\n+	line\n"
	got := ParseAddedLines(diff)
	if _, ok := got["internal/x.go"]; !ok {
		t.Fatalf("a --no-prefix diff parsed as %v", got)
	}
}

func TestParseAddedLinesOnEmptyInput(t *testing.T) {
	if got := ParseAddedLines(""); len(got) != 0 {
		t.Fatalf("empty diff produced %v", got)
	}
}
