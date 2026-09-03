# examples/monorepo — the shared-policy fixture (draft.6 / rev 3.3, round-3 T3-03)

A vbpub-shaped monorepo in v8 notation: one zero-instance **root** `ciu.toml` carrying the estate's
`[governance]`, the ephemeral `tester-unified` environment, the gate slice and the judge floor; a
**command-only child** `libx/` that inherits all of it and uses none of the judge floor; an
**application child** `appy/` with an assay lane and a persistent exec-mode tester whose
`build.context` is the sibling `tester-unified/` directory; the shared `Dockerfile` itself.

What the fixture exercises, rule by rule:

| file | rule | what a reader should see |
|---|---|---|
| `ciu.toml` | S16.11.1 | `[governance]` in a zero-instance project is legal: it governs nothing here and is what the children inherit |
| `libx/ciu.toml`, `appy/ciu.toml` | S1.5, S3.1.5 | each child is its own nearest root (nesting is INFO), and `inherit = "../ciu.toml"` reaches outside that root but inside the worktree |
| `libx/ciu.toml` | S16.3 | an inherited judge floor with no assay lane is unused, not an error |
| `appy/tester/ciu.stack.toml` | S6.2, S5.4 | `build.context = "../../../tester-unified"` leaves the stack and the child root, never the worktree; `location` stays under the child root (a shared directory was rejected — it holds rendered artifacts and the lock) |
| `appy/ciu.toml` | S16.4, S16.5.7 | `exec_in = "tester"` names a capability of THIS instance; one lane at a time on the tester's stack-directory lock |
| both children | S16.6.1 | every lane declares `memory_max` because the inherited slice is finite |
| a release of `appy` | S3.1.5, S17.3.1 | `ciu push` would flatten the root's tables into `ciu.inherited.toml` at the release root; no `../ciu.toml` exists on a target |

Not in the fixture: `assay.toml` for `appy` (assay's own file; the dstdns demo's `assay.toml` shows the shape), a gitignore (the S2.3 list, as in `examples/minimal/.gitignore`). Every file parses as TOML; the fixture is checked by hand until V8-28 makes it executable.
