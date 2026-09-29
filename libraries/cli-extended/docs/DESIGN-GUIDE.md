# cli-extended design guide

This document explains why `cli-extended` is a small adapter layer instead of
a new CLI framework. The observable rules live in the normative
[`SPEC.md`](../SPEC.md); adoption steps live in [`CONSUMERS.md`](CONSUMERS.md).

## Keep the parser convention adopters already know

CIU, CMRU, and the Netcup tools already use `argparse`. Requiring every
consumer to learn a different decorator framework would make migration itself
the dominant risk. `ExtendedArgumentParser` therefore retains argparse's
subparsers, argument groups, choices, and `Namespace` values while adding the
repository rules that argparse does not provide:

- no implicit `-h` option;
- exact long-option spellings, with prefix abbreviation disabled by default;
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
not colour-dependent UI. The normal CLI boundary applies restrained colour to
generated terminal help, severity tags, hints, and shared progress. It uses
the destination stream's TTY state, honors `NO_COLOR` and `--no-color`, and
lets explicit `--color` force presentation. Primary result values, JSON,
version output, and generated Markdown remain plain. Hints are separate from
severity because an actionable remedy is not a logging level. Keeping ANSI
generation in the shared output layer prevents one consumer from coloring
warnings differently or leaking escapes into another consumer's JSON/pipes.

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

Supported common output and debugging options are available on either side of
the selected verb. Some output modes, such as JSON or progress, can be scoped
to only the verbs that implement them. Root help lists the available union;
verb help is authoritative, and the parser refuses an unsupported mode with
that verb's help. For supported options, meaning cannot depend on argument
order or parser nesting: verbosity and colour conflicts are rejected across
the invocation, not resolved by last-option-wins.

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
`mutating` receives the common `--yes` option by default;
`confirmation_required` can state whether that acknowledgement is required
without changing the mutation label. Its default preserves current behavior;
the legacy `include_confirmation` spelling remains accepted for existing
consumers. `OptionSpec.hidden` keeps internal options parseable without
advertising them in user help or generated Markdown.

The shared handler boundary treats `None` as success and returns process
status `0`. Consumers can use that default instead of repeating `return 0` in
every successful handler; an integer return remains available when a product
has a deliberate status contract. Keep that normalization at one boundary so
each handler does not reimplement shell status plumbing.

Mutation and generic confirmation answer different questions. `mutating`
describes the command's effect; `confirmation_required` says whether the
shared `--yes` acknowledgement is part of its consent contract. An application
may already require a specific `--apply` choice, role check, reviewed state
transition, or other operation-specific authorization. Preserve that domain
guard and disable generic confirmation only when it would be redundant or
misleading. Tests must show both that the generic option is unavailable and
that the product guard still refuses an unauthorized mutation. `--yes` is not
a substitute for the product's authorization rules.

The top-level catalog may need a shorter line than command-specific help.
`VerbSpec.summary_description` supplies that concise discovery label without
discarding the full `description` shown for the verb itself.

`VerbSpec.description` is the full description of an individual command. The
registry passes it to argparse and places it after the generated usage line,
separated by blank lines; it is not the overall product description (which is
provided to `CliRegistry`). Keep `synopsis` unset for ordinary commands. The
library derives syntax from positional arguments and required option metadata,
including required mutually-exclusive alternatives, so the help grammar and
parser constraints have one source of truth. Explicit synopsis text is an
exception for syntax that the structured metadata cannot represent.

The top-level catalog is for choosing an operation, not reconstructing its
full invocation syntax: it shows the verb name and short summary, with one
description column aligned across semantic groups. Detailed options and
arguments remain in command help. `CliRegistry.description` gives that catalog
its overall purpose statement, which follows the generic usage syntax. Keeping
these layers separate avoids a dense, uneven command map while preserving exact
syntax where the operator needs it.

Help-bearing invocation failures put the concise error first, then a blank
line, then the normal product identity and complete command help. This gives
operators the reason before the longer discovery content without weakening
the required identity header on help/version output.

## Keep service startup behind argument handling

A CLI entrypoint may also be the command a service manager runs. Keep module
imports and registry construction free of service startup, state writes,
credential reads, and network connections. Put daemon construction in the
selected handler so `--help`, `--version`, and malformed arguments can return
before service work begins. If the documented operation is intentionally a
no-argument service action, `single_command=True` with
`no_args_action=True` expresses that contract while preserving the normal
help/version paths.

The library cannot undo work the consumer performs before calling
`app.run()`. A Nyxloom service launcher initially started the daemon before
processing `--help`; its fix moved argument handling before daemon and registry
imports. Keep the handler lazy and test the installed executable with a
startup sentinel: help, version, and invalid syntax must not set it, while the
valid no-argument service invocation must reach the handler. See the
[Nyxloom adoption record](../../../nyxloom/nyxloom-trove/reports/nyxloom-P114-LOG.md).

The consumer still decides the public vocabulary, behavior labels, examples,
argument constraints, and mutation policy. A registry is a source of truth for
the interface, not a source of domain truth. `configure(parser)` remains an
escape hatch for unusual nested parser structures rather than the default
place for every argument. `CliRegistry(..., allow_abbrev=True)` is available
only for a CLI that deliberately makes partial option spellings part of its
contract; exact matching avoids silently accepting a retired option as the
prefix of a different one.

The black-box contract helper checks observable behavior, not just parser
construction. It rejects the undocumented short `-h` spelling by default,
and its `known_verb_errors` mapping lets a consumer assert that a known command
failure includes the same complete help shown by `help VERB`. A usage line
alone is not enough to make a parse error actionable.

The same registry supports single-command tools during migration. When a tool
has distinct operator actions, explicit verbs make those differences
discoverable and testable rather than hiding them behind a positional UUID or
mode flag.

## A registry is not the product contract

One registry can make parser constraints, help text, and dispatch agree. It
cannot determine whether the product has the right verbs, whether a group
matches the operator's workflow, what omission means, or whether an option's
help matches its side effects. Those are product decisions, so adoption should
revisit the public workflow instead of preserving every old spelling by
default.

Keep the product's semantic CLI inventory beside its normative specification
and update it with every interface change. That record should state each
verb's caller and purpose, accepted selectors and defaults, option
interactions, refused combinations, effects, dry-run and confirmation
boundaries, and output/status contract. Generated help and a registry
inventory test prove that the declared grammar is synchronized; behavior
oracles must still prove that each accepted option does what it claims.

This boundary is especially useful for mixed operations. A syntax library can
express choices, required values, and mutually exclusive options, but a rule
such as “preview is meaningful only when an update was selected” is an
application contract. The consumer must refuse a meaningless combination and
test the refusal before any side effect. Likewise, the library can provide a
default-no confirmation prompt, but only the product can build the complete
plan, validate it, describe it, and ensure `--yes` authorizes exactly that
plan.

An adoption review should also enumerate all distributed callers before
removing an interface. Installed console scripts, active module adapters,
bootstrap and project-step commands, Python libraries, and generated
standalone tools can have different availability and support contracts. CMRU's
review kept its active handler adapter and library APIs, removed unused module
CLI aliases, and retained standalone `get.py` parsing because it runs without
the CMRU wheel.

## Prove the shared contract at each rigor level

The package gate separates ordinary behavior and coverage (R0/R1), mutation
testing (R2), and an independent known-bad-code check (R3). R1 judges the full
shipped package, not only changed lines, and requires both statement and branch
coverage. Its command emits a coverage artifact without imposing its own
failure threshold so the judge remains the owner of the result. R3 uses a
separate disposable source copy and disables a real JSON-redaction guard; the
focused regression test must fail. This keeps a test suite that passes on
correct code from being mistaken for evidence that it detects the defect it
claims to prevent.

The gate is an in-repo consumer and uses the current checkout's Assay source
through `run-gate.py`; it does not pin a stale zipapp. All three lanes execute
inside `tester-unified`. The project gate is reproducible from the same source
and environment used for adoption, while remaining outside the interactive
development cockpit.

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
documentation. It is not the default terminal format: generated Markdown is
always plain and is intended for repository docs and operator guides;
terminal help is styled by the normal CLI boundary only when its color policy
allows it.

The shared confirmation method is default-no and handles non-interactive stdin
and EOF without tracebacks. It cannot know what a mutation means, so consumers
must validate first and call it immediately before performing the exact
validated change. Multi-step prompt flows use the public
`CliOutput.is_interactive` property, which checks both injectable input and
output streams; keeping that test in the shared output object avoids consumers
reaching into terminal-detection internals.

### Optional interactive prompts

Multi-step wizards need the same terminal checks, cancellation behavior, and
test seam across consumers. `CliRuntime.prompts` supplies five small
interactions—text, hidden password, confirmation, single choice, and multiple
choice—through an injectable `PromptDriver`. The default adapter uses
Questionary because it already provides conventional terminal controls; the
shared layer pins it in an optional `interactive` extra and imports it only on
the first prompt. Existing help, version, and automation paths therefore keep
working with the dependency-free base install.

The runtime checks both injected stdin and stdout before loading or calling a
driver. Tests can use a fake `PromptDriver` and TTY-marked in-memory streams,
without starting a terminal or installing Questionary. A canceled prompt raises
`PromptCancelled`, so cancellation cannot be mistaken for a valid false or
empty answer. Ctrl-C remains `KeyboardInterrupt` for the common status-130
boundary.

This boundary intentionally ends at collected values. A consumer still
validates its schema, decides how an answer affects a plan, asks for any
required consent after validation, and commits data atomically. That keeps the
terminal mechanics shared while keeping application policy close to the data
it governs. A consumer that vendors the library can pass its own extra name to
`RegisteredCli.run(interactive_extra="product[interactive]")` for an accurate
missing-dependency hint.
