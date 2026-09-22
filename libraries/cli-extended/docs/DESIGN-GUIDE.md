# cli-extended design guide

This document explains why `cli-extended` is a small adapter layer instead of
a new CLI framework. The observable rules live in the repository-wide
[CLI standard](../../../docs/CLI-STANDARD.md); adoption steps live in
[`CONSUMERS.md`](CONSUMERS.md).

## Keep the parser convention adopters already know

CIU, CMRU, and the Netcup tools already use `argparse`. Requiring every
consumer to learn a different decorator framework would make migration itself
the dominant risk. `ExtendedArgumentParser` therefore retains argparse's
subparsers, argument groups, choices, and `Namespace` values while adding the
repository rules that argparse does not provide:

- no implicit `-h` option;
- the product identity on help and diagnostics;
- complete command help after a known-verb parse error; and
- side-effect-free `help`, `version`, and bare invocation paths.

Click remains a valid choice for a new standalone product, but a Click-based
consumer should still adapt its observable output to the same contract rather
than make the rest of the estate learn Click-specific behavior.

## Keep output streams useful

The command's primary result belongs on stdout. Diagnostics and human progress
belong on stderr. This makes ordinary output composable in a pipe and keeps
`--json` usable without stripping banners from a stream.

Severity tags are plain-text data (`[INFO]`, `[WARN]`, `[ERROR]`, `[DEBUG]`),
not colour-dependent UI. Colour is added only for a human terminal and has
explicit `NO_COLOR`, `--color`, and `--no-color` controls. Hints are separate
from severity because an actionable remedy is not a logging level.

The core package implements this with the standard library. Rich can be used
by a consumer that wants a more elaborate spinner or table, but the contract
must still have the same plain and redirected fallbacks. This keeps the shared
library usable by stdlib-only tools such as CMRU.

## Make verbosity composable

`--log-level` is the canonical level selector. `--quiet` retains warnings and
errors (`warn` threshold); `--debug` and `--verbose` select debug detail. These
are ergonomic aliases, not a second verbosity scale. `--debug-raw` is
intentionally separate because it changes the redaction boundary and emits a
warning. It must never be enabled from a persistent default.

The logging adapter accepts ordinary `logging.Logger` records, so a consumer
can retain its existing logging calls instead of replacing them with a new
logging API. `CliRegistry` scopes logging to a command namespace and restores
its prior state at the end of the invocation; consumers may select the logger
namespace their modules already use. The process-wide root logger is not
reconfigured.

## Declare the interface once

Repeatedly registering a command in a parser, a help list, a handler map, and
a docs page invites drift. `CliRegistry` accepts one `VerbSpec` per public
command and generates parser registration, grouped help, help lookup, and
dispatch from those definitions. `ArgumentSpec` and `OptionSpec` carry
positional/option help, groups, and argparse attributes. A command marked
`mutating` receives the common `--yes` option and confirmation contract;
read-only commands do not.

The consumer still decides the public vocabulary, behavior labels, examples,
argument constraints, and mutation policy. A registry is a source of truth for
the interface, not a source of domain truth. `configure(parser)` remains an
escape hatch for unusual nested parser structures rather than the default
place for every argument.

The same registry supports single-command tools during migration. When a tool
has distinct operator actions, explicit verbs make those differences
discoverable and testable rather than hiding them behind a positional UUID or
mode flag.

## Make long operations automation-safe

Progress is a presentation policy, not the operation's result. `auto` chooses
interactive redraw only for a TTY and plain newline events otherwise;
`rawjson` is explicit JSONL for a machine consumer. The renderer does not
start threads, own a signal handler, or decide when an operation is complete:
the owning command retains control of retries, cancellation, and state
transitions.

## Markdown is a documentation format

`HelpCatalog.render(output_format="markdown")` renders the same verb metadata
and common options used by terminal help into a README-friendly reference,
including examples, behavior labels, arguments, parser choices/defaults, and
grouped options. This avoids a second hand-written command list in
documentation. It is not the default terminal format: shell help stays plain
text, while generated Markdown is intended for repository docs and operator
guides.

The shared confirmation method is default-no and handles non-interactive stdin
and EOF without tracebacks. It cannot know what a mutation means, so consumers
must validate first and call it immediately before performing the exact
validated change.
