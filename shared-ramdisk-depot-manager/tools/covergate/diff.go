package main

import (
	"regexp"
	"strconv"
	"strings"
)

// hunkRe matches a unified-diff hunk header. Only the new side matters; the
// count is optional and defaults to 1 (`@@ -a +c @@`).
var hunkRe = regexp.MustCompile(`^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@`)

// ParseAddedLines walks `git diff --unified=0` output and returns the
// new-side line numbers each file added or edited.
//
// Only the new side counts. A `-` body line is a pure deletion: it advances
// nothing on the new side, and holding a change responsible for covering a
// line it deleted would be incoherent. A deleted file (`+++ /dev/null`)
// contributes nothing at all.
func ParseAddedLines(diff string) map[string]map[int]bool {
	added := make(map[string]map[int]bool)
	current := ""
	newLine := 0

	for _, line := range strings.Split(diff, "\n") {
		switch {
		case strings.HasPrefix(line, "+++ "):
			target := strings.TrimSpace(line[4:])
			if target == "/dev/null" {
				current = ""
				continue
			}
			current = strings.TrimPrefix(target, "b/")
			continue
		case strings.HasPrefix(line, "--- "):
			// Old-side header; never a path this gate counts.
			continue
		}

		if m := hunkRe.FindStringSubmatch(line); m != nil {
			n, err := strconv.Atoi(m[1])
			if err != nil {
				continue
			}
			newLine = n
			continue
		}
		if current == "" {
			continue
		}

		switch {
		case strings.HasPrefix(line, "+"):
			if added[current] == nil {
				added[current] = make(map[int]bool)
			}
			added[current][newLine] = true
			newLine++
		case strings.HasPrefix(line, "-"):
			// Deletion: no new-side advance.
		default:
			// A context line, which only appears at -U>0. Advance so the
			// gate still works if someone runs it with context.
			newLine++
		}
	}
	return added
}
