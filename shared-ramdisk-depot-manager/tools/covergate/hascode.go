package main

import (
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
)

// HasExecutableCode reports whether a Go file declares any function, and so
// whether `go test -cover` could ever produce a block for it.
//
// Go's cover tool instruments function *bodies* and nothing else. A file
// holding only a package clause, imports, comments, types, constants and
// variables therefore yields zero blocks and never appears in a cover
// profile — indistinguishable, from the profile alone, from a file that
// failed to compile or was excluded by a build tag. Parsing settles it.
//
// A file that cannot be read or parsed returns true: the strict reading.
// The gate should fail loudly on a file it cannot understand rather than
// quietly excuse it from coverage.
func HasExecutableCode(root, rel string) bool {
	full := filepath.Join(root, filepath.FromSlash(rel))
	src, err := os.ReadFile(full)
	if err != nil {
		return true
	}
	fset := token.NewFileSet()
	// Skip resolution and comments; only the declaration list matters.
	f, err := parser.ParseFile(fset, full, src, parser.SkipObjectResolution)
	if err != nil {
		return true
	}
	for _, decl := range f.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok {
			continue
		}
		// A declaration with no body is an assembly or cgo stub: it has no
		// Go statements to instrument.
		if fn.Body != nil {
			return true
		}
	}
	return false
}
