// Package hello demonstrates assay's Go adapter (P08) against a real,
// pre-generated coverprofile. This file itself is comment-only -- the
// exact shape of srdm's own historical incident (covergate/evaluate.go's
// own comment: "this gate's own first run flagged 94 lines across four
// comment-only doc.go files as uncovered") -- and is therefore absent from
// the committed hello.out below entirely: go test -coverprofile produces
// zero blocks for a file with no function bodies.
package hello
