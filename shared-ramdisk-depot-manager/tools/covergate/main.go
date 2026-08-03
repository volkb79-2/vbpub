// Command covergate fails when a change adds or edits executable Go lines
// that no test exercises.
//
// This is srdm's answer to D-007. nyxloom ships a changed-line coverage gate
// (`nyxloom/src/nyxloom/coverage_gate.py`) whose *design* is language
// agnostic — a pure added-lines diff walk, a pure intersection against a
// three-way line classification, and a three-outcome result model — but
// whose two I/O seams are coverage.py-shaped. This reimplements those
// semantics for Go, deliberately mirroring the parts that were learned the
// hard way there:
//
//   - **Three-way line classification.** executed / missing / neither. A
//     changed line that is neither is a comment or a brace, and editing it is
//     not an uncovered-code event.
//
//   - **Three outcomes, not two.** Pass (0), fail (1), tool error (2), and —
//     the important one — NO MEASUREMENT (3). "0/0 changed lines covered
//     (100.0%)" reads identically whether the gate measured everything and
//     found nothing, or measured nothing at all. The second is a real failure
//     mode: a dirty tree (the base..HEAD diff cannot see uncommitted work) or
//     a base that resolves to HEAD itself (no delta by construction) both
//     render that exact string. Both are ruled out before any percentage is
//     computed.
//
//   - **Base resolution serves both phases.** A merge commit diffs against
//     its first parent (the merged delta); anything else diffs against
//     merge-base(base, HEAD) (the fork-point delta).
//
// Usage:
//
//	go test ./... -coverpkg=./... -covermode=atomic -coverprofile=cover.out
//	go run ./tools/covergate -profile cover.out -base main -source internal -fail-under 80
package main

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path"
	"strings"
)

// Exit codes. Distinguishing these is the whole point; collapsing 3 into 2
// ("the tool broke, maybe retry") or into 0 ("nothing to fix") is exactly
// the ambiguity this command exists to remove.
const (
	exitPass          = 0
	exitFail          = 1
	exitToolError     = 2
	exitNoMeasurement = 3
)

// errNoMeasurement marks a precondition that makes any percentage vacuous.
var errNoMeasurement = errors.New("no measurement")

func main() {
	code, err := run(os.Args[1:], os.Stdout)
	if err != nil {
		fmt.Fprintf(os.Stderr, "covergate: %v\n", err)
	}
	os.Exit(code)
}

func run(args []string, out *os.File) (int, error) {
	fs := flag.NewFlagSet("covergate", flag.ContinueOnError)
	profilePath := fs.String("profile", "cover.out", "go test -coverprofile output")
	base := fs.String("base", "main", "ref the change is measured against")
	source := fs.String("source", "internal", "source path prefix the gate enforces")
	module := fs.String("module", "srdm", "go module path, stripped from cover profile paths")
	failUnder := fs.Float64("fail-under", 100, "minimum % of changed executable lines covered")
	repo := fs.String("repo", ".", "git worktree to diff in")
	if err := fs.Parse(args); err != nil {
		return exitToolError, err
	}

	baseRev, err := resolveBase(*repo, *base)
	if err != nil {
		return exitToolError, err
	}
	if err := checkMeasurable(*repo, baseRev, *source); err != nil {
		if errors.Is(err, errNoMeasurement) {
			return exitNoMeasurement, err
		}
		return exitToolError, err
	}

	diff, err := git(*repo, "diff", "--relative", "--unified=0", baseRev, "HEAD", "--", *source)
	if err != nil {
		return exitToolError, err
	}
	added := ParseAddedLines(diff)

	f, err := os.Open(*profilePath)
	if err != nil {
		return exitToolError, fmt.Errorf("cannot read cover profile: %w", err)
	}
	defer f.Close()
	raw, err := ParseCoverProfile(f)
	if err != nil {
		return exitToolError, err
	}
	coverage := stripModulePrefix(raw, *module)

	v := Evaluate(added, coverage, *source, ".go", *failUnder, isGoTestFile,
		func(rel string) bool { return HasExecutableCode(*repo, rel) })
	fmt.Fprintln(out, v.Report())
	if !v.Passed() {
		return exitFail, nil
	}
	return exitPass, nil
}

// isGoTestFile reports whether a path is a Go test file. Its own lines are
// not what the gate measures.
func isGoTestFile(p string) bool { return strings.HasSuffix(p, "_test.go") }

// stripModulePrefix rewrites cover-profile keys into worktree-relative
// paths.
//
// A Go cover profile names files by import path (`srdm/internal/store/x.go`)
// while `git diff --relative` names them relative to the invocation
// directory (`internal/store/x.go`). Without this they never compare equal,
// every source file looks absent from the profile, and the gate fails
// everything for a reason that has nothing to do with tests.
func stripModulePrefix(in map[string]*FileCoverage, module string) map[string]*FileCoverage {
	module = strings.TrimSuffix(path.Clean(module), "/")
	out := make(map[string]*FileCoverage, len(in))
	for k, v := range in {
		key := path.Clean(k)
		if module != "" && module != "." {
			key = strings.TrimPrefix(key, module+"/")
		}
		out[key] = v
	}
	return out
}

func git(repo string, args ...string) (string, error) {
	cmd := exec.Command("git", append([]string{"-C", repo}, args...)...)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		msg := strings.TrimSpace(stderr.String())
		if len(msg) > 200 {
			msg = msg[:200]
		}
		return "", fmt.Errorf("git %s failed: %v: %s", strings.Join(args, " "), err, msg)
	}
	return string(out), nil
}

// resolveBase picks the revision the change is measured against.
//
// A merge commit (HEAD has two or more parents) diffs against its FIRST
// parent — exactly the delta the merge introduced, re-verified against the
// merged tree's own test run. Anything else diffs against
// merge-base(base, HEAD), the fork point of the branch. One command, both
// phases.
func resolveBase(repo, base string) (string, error) {
	out, err := git(repo, "rev-list", "--parents", "-n", "1", "HEAD")
	if err != nil {
		return "", err
	}
	if tokens := strings.Fields(out); len(tokens) >= 3 {
		return tokens[1], nil
	}
	mb, err := git(repo, "merge-base", base, "HEAD")
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(mb), nil
}

// checkMeasurable rules out the states in which no percentage means
// anything, before one is computed.
func checkMeasurable(repo, baseRev, source string) error {
	status, err := git(repo, "status", "--porcelain", "--", source)
	if err != nil {
		return err
	}
	var dirty []string
	for _, line := range strings.Split(status, "\n") {
		if len(line) < 4 {
			continue
		}
		p := line[3:]
		if _, after, found := strings.Cut(p, " -> "); found {
			p = after // a rename: only the new path still exists
		}
		dirty = append(dirty, p)
	}
	if len(dirty) > 0 {
		return fmt.Errorf("%w: %d uncommitted path(s) under %s — the gate diffs "+
			"committed HEAD, so these are invisible to it. Commit, then re-run. Affected: %s",
			errNoMeasurement, len(dirty), source, strings.Join(dirty, ", "))
	}

	head, err := git(repo, "rev-parse", "HEAD")
	if err != nil {
		return err
	}
	if strings.TrimSpace(head) == baseRev {
		return fmt.Errorf("%w: the resolved base (%.12s) IS HEAD, so there is no delta to "+
			"measure. --base must resolve to an ancestor of HEAD; check that HEAD is not "+
			"already the tip of that ref", errNoMeasurement, baseRev)
	}
	return nil
}
