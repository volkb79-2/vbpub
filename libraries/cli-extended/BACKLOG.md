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

## CLI-EXT-02 — evaluate declarative conditional option constraints

**Status:** Open

**Type:** Feature investigation

**Area:** Grammar metadata and validation

The registry can express choices, required values, and required or optional
mutually exclusive groups. A consumer may still need rules such as “`--dry-run`
requires `--update`/`--write`/`--refresh`” or “this option applies only when a
particular mode is selected.” CMRU's adoption review found several such
relationships implemented as post-parse handler checks. If an accepted option
has no effect in an unsupported combination, the parser can be syntactically
correct while the CLI contract is semantically false.

Evaluate a structured way to declare simple option dependencies and
forbidden combinations so the registry can reject them consistently and
reflect them in generated help/reference metadata. Keep rules that depend on
loaded product configuration, runtime state, or domain data in the consumer.
Before adding a public API, confirm that the same relationship is needed by
more than one consumer and define how it composes with optional commands,
delegated registries, global options, custom parsers, and synopsis generation.
The consumer still owns the reason for the rule and its user-facing remedy.

**Provenance:** CMRU's semantic audit found selector-gated dry-run options and
mode-dependent helper inputs; see
[`CMRU S-CLI.9`](../../cmru/docs/SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit).

## CLI-EXT-03 — assess a lazy service-entrypoint pattern

**Status:** Open

**Type:** Feature investigation

**Area:** Entrypoint lifecycle

A daemon CLI may intentionally start its service on a valid no-argument
invocation, while `--help`, `--version`, and malformed arguments must remain
side-effect free. The current registry accepts callable handlers, so consumers
can keep service imports inside a lightweight handler and use
`single_command=True, no_args_action=True`. Nyxloom's `nyxloomd --help` was
initially handled after daemon startup and needed a pre-runtime argument path.

Document and validate the lazy-handler pattern first. If multiple adopters
cannot use it cleanly, evaluate a public lazy-handler or service-entrypoint
helper that preserves the shared parser contract without requiring a second
parser. Keep daemon lifecycle, credentials, and state ownership in the
consumer. Any API proposal must prove that help/version/errors do not invoke
the service and that the valid no-argument invocation does.

**Provenance:** Nyxloom P114 found and fixed service startup before argument
handling; see
[`P114 execution log`](../../nyxloom/nyxloom-trove/reports/nyxloom-P114-LOG.md).
