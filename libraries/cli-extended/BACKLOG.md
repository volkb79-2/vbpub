# cli-extended backlog

Open usability gaps for the shared CLI contract layer. Items here are
proposals; they are not scheduled work.

## CLI-EXT-01 — expose the shared colour policy to consumer renderers

**Status:** Open
**Type:** Feature
**Area:** Output and terminal policy

`CliOutput.color_enabled(stream)` already resolves explicit `--color` /
`--no-color`, TTY state, and `NO_COLOR`. The documented contract currently
focuses on colour applied by `app.run()` to help, diagnostics, hints, and
progress. A CLI that owns a styled primary result (for example, a Rich or
Pygments renderer on stdout) has no documented contract for asking the shared
layer whether that destination should receive ANSI, so consumers can duplicate
the policy or rely on a method that is not named in the consumer guidance.

Promote a stream-specific colour-policy query as supported consumer API, either
by documenting and stabilizing `CliOutput.color_enabled(stream)` or by exposing
a small public helper with the same policy. Document how consumers pass that
result into their renderer, how stdout and stderr may differ, and the guarantee
that machine-readable JSON remains free of ANSI. Add a pasteable consumer
example and contract coverage when implemented.

**Provenance:** nyxloom's session extractor uses Rich and Pygments for its own
primary stdout rendering and currently implements terminal detection in its
CLI; this surfaced the adoption gap while reviewing whether it could reuse
cli-extended's existing TTY/`NO_COLOR` policy.
