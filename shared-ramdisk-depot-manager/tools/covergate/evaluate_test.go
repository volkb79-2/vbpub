package main

import (
	"reflect"
	"strings"
	"testing"
)

func lines(nums ...int) map[int]bool {
	m := make(map[int]bool, len(nums))
	for _, n := range nums {
		m[n] = true
	}
	return m
}

func cov(executed, missing []int) *FileCoverage {
	fc := &FileCoverage{Executed: map[int]bool{}, Missing: map[int]bool{}}
	for _, l := range executed {
		fc.Executed[l] = true
	}
	for _, l := range missing {
		fc.Missing[l] = true
	}
	return fc
}

func TestEvaluateCountsOnlyExecutableChangedLines(t *testing.T) {
	added := map[string]map[int]bool{
		"internal/store/store.go": lines(10, 11, 12, 50),
	}
	coverage := map[string]*FileCoverage{
		"internal/store/store.go": cov([]int{10, 11}, []int{12}),
		// line 50 is in neither set: a comment or a blank.
	}

	v := Evaluate(added, coverage, "internal", ".go", 100, isGoTestFile, nil)

	if v.ChangedExecutable != 3 {
		t.Errorf("ChangedExecutable = %d, want 3 (line 50 is not code)", v.ChangedExecutable)
	}
	if v.Covered != 2 {
		t.Errorf("Covered = %d, want 2", v.Covered)
	}
	if got := v.Uncovered["internal/store/store.go"]; !reflect.DeepEqual(got, []int{12}) {
		t.Errorf("Uncovered = %v, want [12]", got)
	}
	if v.Passed() {
		t.Error("a verdict of 2/3 passed a 100% floor")
	}
}

// Editing a comment must never fail the gate. This is the property that
// makes the three-way classification worth having.
func TestEvaluateIgnoresNonExecutableChanges(t *testing.T) {
	added := map[string]map[int]bool{"internal/x.go": lines(1, 2, 3)}
	coverage := map[string]*FileCoverage{"internal/x.go": cov(nil, nil)}

	v := Evaluate(added, coverage, "internal", ".go", 100, isGoTestFile, nil)

	if v.ChangedExecutable != 0 {
		t.Fatalf("ChangedExecutable = %d, want 0", v.ChangedExecutable)
	}
	if !v.Passed() {
		t.Error("a comment-only change failed the gate")
	}
	if v.Pct != 100 {
		t.Errorf("Pct = %v, want 100 for an empty denominator", v.Pct)
	}
}

func TestEvaluateIgnoresPathsOutsideTheSourcePrefix(t *testing.T) {
	added := map[string]map[int]bool{
		"internal/x.go":           lines(10),
		"cmd/srdm/main.go":        lines(10),
		"tools/covergate/main.go": lines(10),
	}
	coverage := map[string]*FileCoverage{
		"internal/x.go": cov([]int{10}, nil),
	}

	v := Evaluate(added, coverage, "internal", ".go", 100, isGoTestFile, nil)

	if v.ChangedExecutable != 1 {
		t.Fatalf("ChangedExecutable = %d, want 1; only internal/ is gated", v.ChangedExecutable)
	}
	if len(v.Unmeasured) != 0 {
		t.Errorf("files outside the prefix were reported unmeasured: %v", v.Unmeasured)
	}
}

// Prefix matching is on whole components, or a rule for "internal" starts
// swallowing "internalx".
func TestEvaluatePrefixMatchesWholeComponents(t *testing.T) {
	added := map[string]map[int]bool{"internalx/x.go": lines(10)}
	coverage := map[string]*FileCoverage{"internalx/x.go": cov(nil, []int{10})}

	v := Evaluate(added, coverage, "internal", ".go", 100, isGoTestFile, nil)
	if v.ChangedExecutable != 0 {
		t.Fatalf("internalx/ was gated by a prefix rule for internal/")
	}
}

// A JSON fixture or a systemd unit beside the code can never appear in a Go
// cover profile. Treating its absence as uncovered would be a verdict no
// test could ever clear: unmeasurABLE is not unmeasurED.
func TestEvaluateIgnoresFilesCoverageCannotMeasure(t *testing.T) {
	added := map[string]map[int]bool{
		"internal/schema.json": lines(1, 2, 3),
		"internal/unit.slice":  lines(1),
	}
	v := Evaluate(added, nil, "internal", ".go", 100, isGoTestFile, nil)

	if v.ChangedExecutable != 0 {
		t.Fatalf("ChangedExecutable = %d; non-Go files are not measurable", v.ChangedExecutable)
	}
	if !v.Passed() {
		t.Error("a data-file change failed a coverage gate")
	}
}

// A Go source file the profile never saw IS a real problem — the gate
// cannot prove its lines ran — but it is a different problem from "a test
// is missing", so it is surfaced separately.
func TestEvaluateFlagsSourceFilesAbsentFromTheProfile(t *testing.T) {
	added := map[string]map[int]bool{"internal/new.go": lines(5, 6)}

	v := Evaluate(added, map[string]*FileCoverage{}, "internal", ".go", 100, isGoTestFile, nil)

	if v.ChangedExecutable != 2 || v.Covered != 0 {
		t.Fatalf("got %d/%d, want 0/2", v.Covered, v.ChangedExecutable)
	}
	if !reflect.DeepEqual(v.Unmeasured, []string{"internal/new.go"}) {
		t.Fatalf("Unmeasured = %v", v.Unmeasured)
	}
	report := v.Report()
	if !strings.Contains(report, "coverpkg") {
		t.Errorf("the report does not point at the likely cause: %s", report)
	}
}

// A test file's own lines are not what the gate measures. Requiring a test
// to be covered by another test is a regress, not a floor.
func TestEvaluateSkipsTestFiles(t *testing.T) {
	added := map[string]map[int]bool{"internal/store/store_test.go": lines(10, 11)}

	v := Evaluate(added, nil, "internal", ".go", 100, isGoTestFile, nil)
	if v.ChangedExecutable != 0 {
		t.Fatalf("ChangedExecutable = %d; a test file's own lines are not gated", v.ChangedExecutable)
	}
}

func TestEvaluateHonoursTheFloor(t *testing.T) {
	added := map[string]map[int]bool{"internal/x.go": lines(1, 2, 3, 4)}
	coverage := map[string]*FileCoverage{"internal/x.go": cov([]int{1, 2, 3}, []int{4})}

	if v := Evaluate(added, coverage, "internal", ".go", 100, isGoTestFile, nil); v.Passed() {
		t.Error("75% passed a 100% floor")
	}
	v := Evaluate(added, coverage, "internal", ".go", 75, isGoTestFile, nil)
	if !v.Passed() {
		t.Errorf("75%% failed a 75%% floor (pct=%v)", v.Pct)
	}
	if !strings.Contains(v.Report(), "OK") {
		t.Errorf("a passing verdict does not read as OK: %s", v.Report())
	}
}

func TestReportNamesEveryUncoveredLine(t *testing.T) {
	added := map[string]map[int]bool{
		"internal/a.go": lines(1, 2),
		"internal/b.go": lines(7),
	}
	coverage := map[string]*FileCoverage{
		"internal/a.go": cov(nil, []int{1, 2}),
		"internal/b.go": cov(nil, []int{7}),
	}

	report := Evaluate(added, coverage, "internal", ".go", 100, isGoTestFile, nil).Report()
	for _, want := range []string{"internal/a.go", "[1 2]", "internal/b.go", "[7]"} {
		if !strings.Contains(report, want) {
			t.Errorf("report is missing %q:\n%s", want, report)
		}
	}
}

func TestUnderPrefix(t *testing.T) {
	cases := []struct {
		p, prefix string
		want      bool
	}{
		{"internal/x.go", "internal", true},
		{"internal", "internal", true},
		{"internalx/x.go", "internal", false},
		{"cmd/x.go", "internal", false},
		{"anything", "", true},
		{"anything", ".", true},
	}
	for _, tc := range cases {
		if got := underPrefix(tc.p, tc.prefix); got != tc.want {
			t.Errorf("underPrefix(%q, %q) = %v, want %v", tc.p, tc.prefix, got, tc.want)
		}
	}
}
