// The statement-position oracle is a stdlib-only main package, so this module
// declares no requirements at all -- which is the point. With no `require`
// lines there is nothing for the module loader to resolve, so the helper runs
// under `GOPROXY=off` with no network and no module cache population, which is
// what lets assay invoke it inside a `--network=none` gate container.
//
// The module PATH is deliberately under a reserved-for-testing TLD
// (RFC 2606 `.invalid`) so it can never collide with, or be mistaken for, a
// real published module.
//
// The `go` line is a FLOOR, not a pin. It is the language version the source
// is written against; the toolchain that actually runs is the gate image's,
// recorded per verdict in `helpers[].identity` (`go version ...`) rather than
// assumed from this line. `GOTOOLCHAIN=local` in the image stops the toolchain
// silently upgrading itself past it.
module assay.invalid/stmtpos

go 1.21
