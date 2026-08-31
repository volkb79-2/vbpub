// Command stmtpos is assay's Go statement-position oracle.
//
// It answers exactly one question, for one or more Go source files: for every
// coverage block `go test -coverprofile` will emit over this source, WHICH
// PHYSICAL LINES BEGIN A COUNTED STATEMENT inside that block.
//
// # Why this program exists
//
// A Go cover profile record is
//
//	<path>:<startLine>.<startCol>,<endLine>.<endCol> <numStmts> <count>
//
// which carries a positional EXTENT plus a statement CARDINALITY -- never the
// statements' own positions. Assay's parser used to expand that extent with
// `range(startLine, endLine+1)`, which attributes function signatures, closing
// braces, comments and statement-continuation lines as if they were code.
//
// That expansion is not merely imprecise, it is IMPOSSIBLE to fix from the
// profile alone. `carve-assets/P27/witness/collision-colA.go` and
// `collision-colB.go` are both gofmt-clean, both compile under the pinned
// toolchain, and emit a BYTE-IDENTICAL profile
//
//	example.invalid/coll/f.go:3.22,7.2 2 1
//
// while their statements begin on different lines -- {4,6} for A, {4,5} for B.
// A rule is a function of its input; identical input forces identical output;
// the two correct answers differ; therefore every profile-only rule is wrong on
// at least one of them. (assay decision A-217, evidence in
// `carve-assets/P27/BLOCKED-grammar.md` §1.)
//
// The only way to recover statement positions is to re-derive them from the
// SOURCE, which is what this program does.
//
// # Provenance: adapted, not invented
//
// The algorithm that PRODUCES those blocks is `cmd/cover`'s own instrumenter --
// `golang/go`, `src/cmd/cover/cover.go`, BSD-3-Clause. A-217's own
// implementation note is "adapt, do not invent", and this file follows it
// literally: `Visit`, `addCounters`, `statementBoundary`, `endsBasicSourceBlock`,
// `isControl`, `hasFuncLiteral`, `funcLitFinder`, `findText`, `offset` and
// `dedup` below are transcribed from that file, with exactly one behavioural
// change -- where cover INSERTS a counter statement into an edit buffer, this
// program RECORDS the block's extent together with the positions of the
// statements that counter counts (`list[0:last]`, the same slice whose length
// cover passes as `numStmt`). Nothing about the segmentation is re-derived.
//
// Deliberately NOT used: `golang.org/x/tools/cover`'s public
// `Profile.Boundaries()`. It interpolates byte offsets between block positions
// and does no AST work at all, so it cannot answer this question either.
//
// # Determinism, and how a caller checks it
//
// The instrumenter is deterministic: re-running it over the same source
// reproduces the same blocks byte-for-byte. That is what makes this program's
// output CHECKABLE rather than merely plausible -- the emitted extents must
// match the profile's extents exactly, and the emitted statement count must
// match the profile's `numStmts`. Assay joins on the extent and refuses loudly
// when either disagrees, rather than attributing lines from a mismatched pair.
//
// # Known non-applicability, stated rather than silently ignored
//
// `cover.go`'s `Visit` skips `AddUint32`/`StoreUint32` when instrumenting
// `sync/atomic` ITSELF in atomic mode (an anti-recursion guard, golang/go
// #57445). That case is not reproduced here because this program is never
// pointed at the Go standard library's own `sync/atomic` package -- assay
// judges a consumer's changed lines, not the toolchain's source. If it ever
// were, the two functions' blocks would be emitted here and absent from the
// profile, which assay's extent join reports as a mismatch rather than
// silently mis-attributing.
//
// # Interface
//
// Usage:
//
//	stmtpos <file.go> [<file.go> ...]
//
// Files are processed in the order given, which matters only for `dedup`
// (below), exactly as the order `cover` annotates a package's files matters
// there. Writes one JSON document to stdout:
//
//	{
//	  "schema": 1,
//	  "go_version": "go1.25.14",
//	  "files": [
//	    {"path": "...", "blocks": [
//	      {"start_line": 3, "start_col": 22, "end_line": 7, "end_col": 2,
//	       "num_stmts": 2, "stmt_lines": [4, 6]}
//	    ]}
//	  ]
//	}
//
// `stmt_lines` is sorted and DEDUPLICATED, so it can be shorter than
// `num_stmts` when two counted statements share one physical line. That is a
// real property of the source, not a defect: `x := 1; y := 2` is two statements
// on one line. Assay compares `num_stmts` against the profile, never
// `len(stmt_lines)`.
//
// Exit status is 0 on success and 1 on any failure (unreadable file, syntax
// error), with a diagnostic on stderr and NOTHING on stdout -- a partial
// document is never emitted, because a caller that got half an answer would
// have no way to tell.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"runtime"
	"sort"
)

// outputSchema is the version of the JSON document this program writes. Assay
// pins it and refuses an unrecognised value, so a future change to the shape
// cannot be read as the shape assay was written against.
const outputSchema = 1

// Block is one coverage block: the extent `go test -coverprofile` will report,
// plus the lines that begin the statements that block's counter counts.
type Block struct {
	StartLine int   `json:"start_line"`
	StartCol  int   `json:"start_col"`
	EndLine   int   `json:"end_line"`
	EndCol    int   `json:"end_col"`
	NumStmts  int   `json:"num_stmts"`
	StmtLines []int `json:"stmt_lines"`
}

// FileResult is one source file's blocks, in the order cover generates them.
type FileResult struct {
	Path   string  `json:"path"`
	Blocks []Block `json:"blocks"`
}

// Output is the whole document.
type Output struct {
	Schema    int          `json:"schema"`
	GoVersion string       `json:"go_version"`
	Files     []FileResult `json:"files"`
}

// File mirrors cover.go's own `File`, reduced to what block recording needs:
// no edit buffer, no counter variables, no package metadata.
type File struct {
	fset    *token.FileSet
	content []byte
	blocks  []Block
}

// ---------------------------------------------------------------------------
// Transcribed from golang/go src/cmd/cover/cover.go (BSD-3-Clause).
// ---------------------------------------------------------------------------

// offset translates a token position into a 0-indexed byte offset.
func (f *File) offset(pos token.Pos) int {
	return f.fset.Position(pos).Offset
}

// findText finds text in the original source, starting at pos. It correctly
// skips over comments and assumes it need not handle quoted strings. It
// returns a byte offset within f.content.
func (f *File) findText(pos token.Pos, text string) int {
	b := []byte(text)
	start := f.offset(pos)
	i := start
	s := f.content
	for i < len(s) {
		if bytes.HasPrefix(s[i:], b) {
			return i
		}
		if i+2 <= len(s) && s[i] == '/' && s[i+1] == '/' {
			for i < len(s) && s[i] != '\n' {
				i++
			}
			continue
		}
		if i+2 <= len(s) && s[i] == '/' && s[i+1] == '*' {
			for i += 2; ; i++ {
				if i+2 > len(s) {
					return 0
				}
				if s[i] == '*' && s[i+1] == '/' {
					i += 2
					break
				}
			}
			continue
		}
		i++
	}
	return -1
}

// Visit implements the ast.Visitor interface.
//
// Transcribed from cover.go's `Visit` with the counter-insertion edits removed.
// The `*ast.IfStmt` else-handling MUTATES the AST exactly as cover does --
// moving a plain else block's `Lbrace` to just past the `else` keyword, and
// wrapping an `else if` in a synthetic block -- because those mutations are
// what determine the recorded extents, not merely where a counter is written.
//
// The `*ast.FuncDecl`/`*ast.FuncLit` cases drop cover's `-pkgcfg`
// bookkeeping (`preFunc`/`postFunc`, per-function granularity) and its
// `sync/atomic` anti-recursion guard; see the package comment for why the
// latter cannot apply here. Neither affects which blocks are produced.
func (f *File) Visit(node ast.Node) ast.Visitor {
	switch n := node.(type) {
	case *ast.BlockStmt:
		// If it's a switch or select, the body is a list of case clauses;
		// don't tag the block itself.
		if len(n.List) > 0 {
			switch n.List[0].(type) {
			case *ast.CaseClause: // switch
				for _, n := range n.List {
					clause := n.(*ast.CaseClause)
					f.addCounters(clause.Colon+1, clause.Colon+1, clause.End(), clause.Body, false)
				}
				return f
			case *ast.CommClause: // select
				for _, n := range n.List {
					clause := n.(*ast.CommClause)
					f.addCounters(clause.Colon+1, clause.Colon+1, clause.End(), clause.Body, false)
				}
				return f
			}
		}
		// +1 to step past closing brace.
		f.addCounters(n.Lbrace, n.Lbrace+1, n.Rbrace+1, n.List, true)
	case *ast.IfStmt:
		if n.Init != nil {
			ast.Walk(f, n.Init)
		}
		ast.Walk(f, n.Cond)
		ast.Walk(f, n.Body)
		if n.Else == nil {
			return nil
		}
		// The elses are special, because if we have
		//	if x {
		//	} else if y {
		//	}
		// we want to cover the "if y". To do this, we need a place to drop
		// the counter, so cover adds a hidden block:
		//	if x {
		//	} else {
		//		if y {
		//		}
		//	}
		elseOffset := f.findText(n.Body.End(), "else")
		if elseOffset < 0 {
			panic("lost else")
		}
		// Adjust the position of the new block to start after the "else".
		pos := f.fset.File(n.Body.End()).Pos(elseOffset + 4)
		switch stmt := n.Else.(type) {
		case *ast.IfStmt:
			block := &ast.BlockStmt{
				Lbrace: pos,
				List:   []ast.Stmt{stmt},
				Rbrace: stmt.End(),
			}
			n.Else = block
		case *ast.BlockStmt:
			stmt.Lbrace = pos
		default:
			panic("unexpected node type in if")
		}
		ast.Walk(f, n.Else)
		return nil
	case *ast.SelectStmt:
		// Don't annotate an empty select - creates a syntax error.
		if n.Body == nil || len(n.Body.List) == 0 {
			return nil
		}
	case *ast.SwitchStmt:
		// Don't annotate an empty switch - creates a syntax error.
		if n.Body == nil || len(n.Body.List) == 0 {
			if n.Init != nil {
				ast.Walk(f, n.Init)
			}
			if n.Tag != nil {
				ast.Walk(f, n.Tag)
			}
			return nil
		}
	case *ast.TypeSwitchStmt:
		// Don't annotate an empty type switch - creates a syntax error.
		if n.Body == nil || len(n.Body.List) == 0 {
			if n.Init != nil {
				ast.Walk(f, n.Init)
			}
			ast.Walk(f, n.Assign)
			return nil
		}
	case *ast.FuncDecl:
		// Don't annotate functions with blank names - they cannot be
		// executed. Similarly for bodyless funcs.
		if n.Name.Name == "_" || n.Body == nil {
			return nil
		}
		ast.Walk(f, n.Body)
		return nil
	case *ast.FuncLit:
		ast.Walk(f, n.Body)
		return nil
	}
	return f
}

// addCounters takes a list of statements and records one block per basic block
// at the top level of that list.
//
// Transcribed from cover.go's `addCounters`. The ONLY change: where cover calls
// `f.edit.Insert(..., f.newCounter(pos, end, last)+";")` this calls
// `f.record(pos, end, list[0:last])` -- the same extent, and the very slice
// whose length cover passes as `numStmt`.
func (f *File) addCounters(pos, insertPos, blockEnd token.Pos, list []ast.Stmt, extendToClosingBrace bool) {
	// Special case: make sure we add a counter to an empty block. Can't do
	// this below or we will add a counter to an empty statement list after,
	// say, a return statement.
	if len(list) == 0 {
		f.record(insertPos, blockEnd, nil)
		return
	}
	// Make a copy of the list, as we may mutate it and should leave the
	// existing list intact.
	list = append([]ast.Stmt(nil), list...)
	// We have a block (statement list), but it may have several basic blocks
	// due to the appearance of statements that affect the flow of control.
	for {
		// Find first statement that affects flow of control (break, continue,
		// if, etc.). It will be the last statement of this basic block.
		var last int
		end := blockEnd
		for last = 0; last < len(list); last++ {
			stmt := list[last]
			end = f.statementBoundary(stmt)
			if f.endsBasicSourceBlock(stmt) {
				// If it is a labeled statement, we need to place a counter
				// between the label and its statement because it may be the
				// target of a goto and thus start a basic block.
				if label, isLabel := stmt.(*ast.LabeledStmt); isLabel && !f.isControl(label.Stmt) {
					newLabel := *label
					newLabel.Stmt = &ast.EmptyStmt{
						Semicolon: label.Stmt.Pos(),
						Implicit:  true,
					}
					end = label.Pos() // Previous block ends before the label.
					list[last] = &newLabel
					// Open a gap and drop in the old statement, now without
					// a label.
					list = append(list, nil)
					copy(list[last+1:], list[last:])
					list[last+1] = label.Stmt
				}
				last++
				extendToClosingBrace = false // Block is broken up now.
				break
			}
		}
		if extendToClosingBrace {
			end = blockEnd
		}
		if pos != end { // Can have no source to cover if e.g. blocks abut.
			f.record(pos, end, list[0:last])
		}
		list = list[last:]
		if len(list) == 0 {
			break
		}
		pos = list[0].Pos()
		insertPos = pos
	}
}

// hasFuncLiteral reports the existence and position of the first func literal
// in the node, if any.
func hasFuncLiteral(n ast.Node) (bool, token.Pos) {
	if n == nil {
		return false, 0
	}
	var literal funcLitFinder
	ast.Walk(&literal, n)
	return literal.found(), token.Pos(literal)
}

// statementBoundary finds the location in s that terminates the current basic
// block in the source.
func (f *File) statementBoundary(s ast.Stmt) token.Pos {
	// Control flow statements are easy.
	switch s := s.(type) {
	case *ast.BlockStmt:
		// Treat blocks like basic blocks to avoid overlapping counters.
		return s.Lbrace
	case *ast.IfStmt:
		found, pos := hasFuncLiteral(s.Init)
		if found {
			return pos
		}
		found, pos = hasFuncLiteral(s.Cond)
		if found {
			return pos
		}
		return s.Body.Lbrace
	case *ast.ForStmt:
		found, pos := hasFuncLiteral(s.Init)
		if found {
			return pos
		}
		found, pos = hasFuncLiteral(s.Cond)
		if found {
			return pos
		}
		found, pos = hasFuncLiteral(s.Post)
		if found {
			return pos
		}
		return s.Body.Lbrace
	case *ast.LabeledStmt:
		return f.statementBoundary(s.Stmt)
	case *ast.RangeStmt:
		found, pos := hasFuncLiteral(s.X)
		if found {
			return pos
		}
		return s.Body.Lbrace
	case *ast.SwitchStmt:
		found, pos := hasFuncLiteral(s.Init)
		if found {
			return pos
		}
		found, pos = hasFuncLiteral(s.Tag)
		if found {
			return pos
		}
		return s.Body.Lbrace
	case *ast.SelectStmt:
		return s.Body.Lbrace
	case *ast.TypeSwitchStmt:
		found, pos := hasFuncLiteral(s.Init)
		if found {
			return pos
		}
		return s.Body.Lbrace
	}
	// If not a control flow statement, it is a declaration, expression, call,
	// etc. and it may have a function literal. If it does, that's tricky
	// because we want to exclude the body of the function from this block.
	// Draw a line at the start of the body of the first function literal we
	// find.
	found, pos := hasFuncLiteral(s)
	if found {
		return pos
	}
	return s.End()
}

// endsBasicSourceBlock reports whether s changes the flow of control: break,
// if, etc., or if it's just problematic, for instance contains a function
// literal, which will complicate accounting due to the block-within-an
// expression.
func (f *File) endsBasicSourceBlock(s ast.Stmt) bool {
	switch s := s.(type) {
	case *ast.BlockStmt:
		// Treat blocks like basic blocks to avoid overlapping counters.
		return true
	case *ast.BranchStmt:
		return true
	case *ast.ForStmt:
		return true
	case *ast.IfStmt:
		return true
	case *ast.LabeledStmt:
		return true // A goto may branch here, starting a new basic block.
	case *ast.RangeStmt:
		return true
	case *ast.SwitchStmt:
		return true
	case *ast.SelectStmt:
		return true
	case *ast.TypeSwitchStmt:
		return true
	case *ast.ExprStmt:
		// Calls to panic change the flow.
		if call, ok := s.X.(*ast.CallExpr); ok {
			if ident, ok := call.Fun.(*ast.Ident); ok && ident.Name == "panic" && len(call.Args) == 1 {
				return true
			}
		}
	}
	found, _ := hasFuncLiteral(s)
	return found
}

// isControl reports whether s is a control statement that, if labeled, cannot
// be separated from its label.
func (f *File) isControl(s ast.Stmt) bool {
	switch s.(type) {
	case *ast.ForStmt, *ast.RangeStmt, *ast.SwitchStmt, *ast.SelectStmt, *ast.TypeSwitchStmt:
		return true
	}
	return false
}

// funcLitFinder implements the ast.Visitor pattern to find the location of any
// function literal in a subtree.
type funcLitFinder token.Pos

func (f *funcLitFinder) Visit(node ast.Node) (w ast.Visitor) {
	if f.found() {
		return nil // Prune search.
	}
	switch n := node.(type) {
	case *ast.FuncLit:
		*f = funcLitFinder(n.Body.Lbrace)
		return nil // Prune search.
	}
	return f
}

func (f *funcLitFinder) found() bool {
	return token.Pos(*f) != token.NoPos
}

// pos2 is a pair of token.Position values, used as a map key type.
type pos2 struct {
	p1, p2 token.Position
}

// seenPos2 tracks whether we have seen a token.Position pair.
//
// Package-level, exactly as in cover.go, and therefore shared across every
// file named on one command line -- which is why files are processed in the
// order given: cover's own map is likewise shared across the files of the
// package it annotates in one run.
var seenPos2 = make(map[pos2]bool)

// dedup takes a token.Position pair and returns a pair that does not duplicate
// any existing pair. The returned pair will have the Offset fields cleared.
func dedup(p1, p2 token.Position) (r1, r2 token.Position) {
	key := pos2{
		p1: p1,
		p2: p2,
	}

	// We want to ignore the Offset fields in the map, since cover uses only
	// file/line/column.
	key.p1.Offset = 0
	key.p2.Offset = 0

	for seenPos2[key] {
		key.p2.Column++
	}
	seenPos2[key] = true

	return key.p1, key.p2
}

// ---------------------------------------------------------------------------
// assay's own additions.
// ---------------------------------------------------------------------------

// record stores one block: the extent cover would have written into the
// profile, and the lines on which the statements that block counts begin.
//
// `dedup` is applied for the same reason cover applies it on the `-pkgcfg`
// path that `go test -cover` uses today: two blocks may otherwise carry an
// identical (start, end) pair, and the profile's own records are keyed by that
// pair. Applying it here keeps the extents assay joins on identical to the
// extents the profile carries.
func (f *File) record(start, end token.Pos, stmts []ast.Stmt) {
	stpos := f.fset.Position(start)
	enpos := f.fset.Position(end)
	stpos, enpos = dedup(stpos, enpos)

	seen := make(map[int]bool, len(stmts))
	for _, stmt := range stmts {
		seen[f.fset.Position(stmt.Pos()).Line] = true
	}
	lines := make([]int, 0, len(seen))
	for line := range seen {
		lines = append(lines, line)
	}
	sort.Ints(lines)

	f.blocks = append(f.blocks, Block{
		StartLine: stpos.Line,
		StartCol:  stpos.Column,
		EndLine:   enpos.Line,
		EndCol:    enpos.Column,
		NumStmts:  len(stmts),
		StmtLines: lines,
	})
}

func analyze(fset *token.FileSet, path string) (FileResult, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return FileResult{}, err
	}
	parsed, err := parser.ParseFile(fset, path, content, parser.ParseComments)
	if err != nil {
		return FileResult{}, err
	}
	file := &File{fset: fset, content: content, blocks: []Block{}}
	ast.Walk(file, parsed)
	return FileResult{Path: path, Blocks: file.blocks}, nil
}

func main() {
	args := os.Args[1:]
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "stmtpos: usage: stmtpos <file.go> [<file.go> ...]")
		os.Exit(1)
	}

	fset := token.NewFileSet()
	out := Output{Schema: outputSchema, GoVersion: runtime.Version(), Files: []FileResult{}}
	for _, path := range args {
		result, err := analyze(fset, path)
		if err != nil {
			// Nothing on stdout: a partial document would be
			// indistinguishable from a complete one.
			fmt.Fprintf(os.Stderr, "stmtpos: %s: %v\n", path, err)
			os.Exit(1)
		}
		out.Files = append(out.Files, result)
	}

	encoded, err := json.Marshal(out)
	if err != nil {
		fmt.Fprintf(os.Stderr, "stmtpos: encoding output: %v\n", err)
		os.Exit(1)
	}
	os.Stdout.Write(append(encoded, '\n'))
}
