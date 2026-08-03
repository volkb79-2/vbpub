package main

import (
	"fmt"
	"path"
	"sort"
	"strings"
)

// Verdict is the outcome of intersecting a change with a coverage report.
type Verdict struct {
	// Uncovered maps a source path to the changed lines that are executable
	// but never ran.
	Uncovered map[string][]int
	// ChangedExecutable is the denominator: changed lines the coverage
	// report deems code.
	ChangedExecutable int
	// Covered is the numerator.
	Covered int
	// Pct is 100*Covered/ChangedExecutable, or 100 when nothing changed.
	Pct float64
	// FailUnder is the floor this verdict was measured against.
	FailUnder float64
	// Unmeasured lists changed source files the coverage report never saw
	// at all. Their changed lines count as uncovered — the gate cannot
	// prove they ran — and they are surfaced separately because the remedy
	// differs: a missing file usually means the build tags or -coverpkg are
	// wrong, not that a test is missing.
	Unmeasured []string
	// NoCode lists changed source files that declare no functions, so they
	// can produce no cover blocks at all. They are excluded from the ratio
	// entirely rather than counted as uncovered.
	NoCode []string
	// Source is the prefix that was gated, and Considered counts the changed
	// files that reached the intersection. Together they let an empty
	// denominator explain itself: "0/0 (100.0%)" is a legitimate verdict for
	// a test-only or comment-only change, but it is textually identical to
	// the one a broken measurement produces, and a reader deserves to know
	// which they are looking at without re-deriving it.
	Source     string
	Considered int
}

// Passed reports the verdict.
func (v Verdict) Passed() bool { return v.Pct >= v.FailUnder }

// Evaluate is the pure heart: intersect changed lines with the coverage
// classification. It takes plain data — no git, no filesystem — so it is
// testable against crafted inputs.
//
// A changed line counts toward the denominator only if the report deems it
// executable. Changed lines in files outside sourcePrefix (tests, docs,
// tooling) are ignored: the gate enforces coverage of SOURCE, not of
// everything a commit touched.
//
// Files with the wrong extension are ignored too, and that distinction
// matters more than it looks. A JSON fixture or a systemd unit living beside
// the code can never appear in a Go cover profile, so treating its absence
// as "unmeasured, therefore uncovered" would produce a verdict no test could
// ever clear. Unmeasur*able* is not unmeasur*ed*.
//
// hasCode is the same distinction one layer in, and it is not hypothetical:
// this gate's own first run flagged 94 lines across four comment-only
// doc.go files as uncovered. `go test -cover` instruments function bodies,
// so a .go file declaring no functions produces no blocks and is absent
// from the profile for exactly the same reason a JSON schema is — it has no
// executable code, not no tests. Callers pass a real parser; a nil hasCode
// means "assume every .go file has code", which is the strict reading.
func Evaluate(
	added map[string]map[int]bool,
	coverage map[string]*FileCoverage,
	sourcePrefix string,
	ext string,
	failUnder float64,
	isTestFile func(string) bool,
	hasCode func(string) bool,
) Verdict {
	v := Verdict{Uncovered: map[string][]int{}, FailUnder: failUnder, Source: sourcePrefix}

	paths := make([]string, 0, len(added))
	for p := range added {
		paths = append(paths, p)
	}
	sort.Strings(paths)

	for _, p := range paths {
		norm := path.Clean(p)
		if sourcePrefix != "" && !underPrefix(norm, sourcePrefix) {
			continue
		}
		if ext != "" && !strings.HasSuffix(norm, ext) {
			continue
		}
		// A test file's own lines are not the thing being gated. Requiring a
		// test to be covered by another test is a regress, not a floor.
		if isTestFile != nil && isTestFile(norm) {
			continue
		}

		v.Considered++
		lines := added[p]
		fc := coverage[norm]
		if fc == nil {
			if hasCode != nil && !hasCode(norm) {
				// No functions, so no cover blocks could ever exist. Absent
				// for the same reason a data file is absent.
				v.NoCode = append(v.NoCode, norm)
				continue
			}
			nums := sortedKeys(lines)
			v.Uncovered[norm] = nums
			v.ChangedExecutable += len(nums)
			v.Unmeasured = append(v.Unmeasured, norm)
			continue
		}

		var uncovered []int
		for _, l := range sortedKeys(lines) {
			if !fc.Executable(l) {
				continue // not code — a comment, a blank, a brace
			}
			v.ChangedExecutable++
			if fc.Executed[l] {
				v.Covered++
				continue
			}
			uncovered = append(uncovered, l)
		}
		if len(uncovered) > 0 {
			v.Uncovered[norm] = uncovered
		}
	}

	sort.Strings(v.Unmeasured)
	sort.Strings(v.NoCode)
	if v.ChangedExecutable == 0 {
		v.Pct = 100
	} else {
		v.Pct = 100 * float64(v.Covered) / float64(v.ChangedExecutable)
	}
	return v
}

// underPrefix reports whether p lies at or beneath prefix, matching on whole
// path components so "internal" does not match "internalx".
func underPrefix(p, prefix string) bool {
	prefix = path.Clean(prefix)
	if prefix == "." || prefix == "" {
		return true
	}
	return p == prefix || strings.HasPrefix(p, prefix+"/")
}

func sortedKeys(m map[int]bool) []int {
	out := make([]int, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Ints(out)
	return out
}

// Report renders a human-readable verdict.
func (v Verdict) Report() string {
	var sb strings.Builder
	if v.Passed() {
		if v.ChangedExecutable == 0 {
			// Say WHY the denominator is empty. This verdict is legitimate
			// for a test-only or comment-only change, but it is textually
			// identical to the one a measurement that never happened would
			// print, and that ambiguity is the whole reason exit 3 exists.
			fmt.Fprintf(&sb, "diff-coverage OK: nothing to cover — %d changed file(s) under %q, "+
				"none contributing executable non-test lines (0/0)", v.Considered, v.Source)
			return sb.String()
		}
		fmt.Fprintf(&sb, "diff-coverage OK: %d/%d changed executable lines covered (%.1f%% >= %.1f%% floor)",
			v.Covered, v.ChangedExecutable, v.Pct, v.FailUnder)
		return sb.String()
	}
	fmt.Fprintf(&sb, "diff-coverage FAIL: %d/%d changed executable lines covered (%.1f%% < %.1f%% floor)\n",
		v.Covered, v.ChangedExecutable, v.Pct, v.FailUnder)
	sb.WriteString("Uncovered changed lines:\n")

	unmeasured := make(map[string]bool, len(v.Unmeasured))
	for _, p := range v.Unmeasured {
		unmeasured[p] = true
	}
	paths := make([]string, 0, len(v.Uncovered))
	for p := range v.Uncovered {
		paths = append(paths, p)
	}
	sort.Strings(paths)
	for _, p := range paths {
		tag := ""
		if unmeasured[p] {
			tag = " [file absent from the cover profile — check -coverpkg and build tags]"
		}
		fmt.Fprintf(&sb, "  %s:%s %v\n", p, tag, v.Uncovered[p])
	}
	sb.WriteString("Add a test that exercises these lines.")
	return sb.String()
}
