package main

import (
	"bufio"
	"fmt"
	"io"
	"strconv"
	"strings"
)

// FileCoverage classifies a file's lines the way a diff-coverage gate needs
// them: three-way, not two.
//
// The three-way split is the load-bearing part. A changed line that is in
// NEITHER set is not code — a comment, a blank, a brace — and editing it is
// not an uncovered-code event. Collapsing that into "not executed" is what
// makes a naive gate fail on a docstring edit.
type FileCoverage struct {
	Executed map[int]bool
	Missing  map[int]bool
}

// Executable reports whether the line is code at all.
func (f FileCoverage) Executable(line int) bool {
	return f.Executed[line] || f.Missing[line]
}

// ParseCoverProfile reads `go test -coverprofile` output.
//
// The format is one block per line:
//
//	<path>:<startLine>.<startCol>,<endLine>.<endCol> <numStmts> <count>
//
// Go's profile is **block**-based, not line-based, so a block spans a range
// of lines and every line in that range is executable. A line can appear in
// more than one block (`} else {` closes one and opens another), and the
// rule there is that executed wins: the line did run, whichever block gets
// the credit. Resolving it the other way would report a line as uncovered
// while the code on it demonstrably executed.
func ParseCoverProfile(r io.Reader) (map[string]*FileCoverage, error) {
	out := make(map[string]*FileCoverage)
	sc := bufio.NewScanner(r)
	// Cover profiles for a large tree have long lines only rarely, but a
	// generous buffer costs nothing and a truncated profile would silently
	// under-report coverage.
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)

	lineNo := 0
	for sc.Scan() {
		lineNo++
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, "mode:") {
			continue
		}
		path, start, end, count, err := parseBlock(line)
		if err != nil {
			return nil, fmt.Errorf("cover profile line %d: %w", lineNo, err)
		}
		fc := out[path]
		if fc == nil {
			fc = &FileCoverage{Executed: map[int]bool{}, Missing: map[int]bool{}}
			out[path] = fc
		}
		for l := start; l <= end; l++ {
			if count > 0 {
				fc.Executed[l] = true
				// A line credited as executed must not also be reported
				// missing by an overlapping never-taken block.
				delete(fc.Missing, l)
				continue
			}
			if !fc.Executed[l] {
				fc.Missing[l] = true
			}
		}
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("reading cover profile: %w", err)
	}
	return out, nil
}

// parseBlock splits one profile record.
func parseBlock(line string) (path string, startLine, endLine, count int, err error) {
	fields := strings.Fields(line)
	if len(fields) != 3 {
		return "", 0, 0, 0, fmt.Errorf("want 3 fields, got %d: %q", len(fields), line)
	}

	// A path may itself contain a colon, so the position spec is delimited by
	// the LAST colon rather than the first.
	idx := strings.LastIndex(fields[0], ":")
	if idx < 0 {
		return "", 0, 0, 0, fmt.Errorf("no position in %q", fields[0])
	}
	path = fields[0][:idx]
	spec := fields[0][idx+1:]

	startSpec, endSpec, ok := strings.Cut(spec, ",")
	if !ok {
		return "", 0, 0, 0, fmt.Errorf("no range in %q", spec)
	}
	if startLine, err = parsePos(startSpec); err != nil {
		return "", 0, 0, 0, err
	}
	if endLine, err = parsePos(endSpec); err != nil {
		return "", 0, 0, 0, err
	}
	if endLine < startLine {
		return "", 0, 0, 0, fmt.Errorf("block ends (%d) before it starts (%d)", endLine, startLine)
	}
	if count, err = strconv.Atoi(fields[2]); err != nil {
		return "", 0, 0, 0, fmt.Errorf("bad count %q: %w", fields[2], err)
	}
	if count < 0 {
		return "", 0, 0, 0, fmt.Errorf("negative count %d", count)
	}
	return path, startLine, endLine, count, nil
}

// parsePos reads the line number out of a `<line>.<col>` position.
func parsePos(s string) (int, error) {
	lineStr, _, ok := strings.Cut(s, ".")
	if !ok {
		return 0, fmt.Errorf("bad position %q", s)
	}
	n, err := strconv.Atoi(lineStr)
	if err != nil {
		return 0, fmt.Errorf("bad line number in %q: %w", s, err)
	}
	if n <= 0 {
		return 0, fmt.Errorf("line number %d in %q is not positive", n, s)
	}
	return n, nil
}
