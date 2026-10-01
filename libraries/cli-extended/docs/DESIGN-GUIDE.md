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
[command ownership guidance](../../../nyxloom/docs/CLI-REFERENCE.md#command-ownership).

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

## Keep a generated surface and a human semantic record

A single full TOML/JSON CLI definition would still need Python handlers and
custom parser callbacks. It would introduce a second executable grammar and
force adopters to prove that the registry and external file stay synchronized.
Keep `CliRegistry` as the source for parser registration, help, and dispatch.
Export its built argparse tree after callbacks have run so callback-added
syntax is visible, and mark fields the exporter cannot enumerate as
incomplete. An ordinary callback can add inspectable argparse actions. A
callback or parser subclass that replaces token-parsing methods, changes
parser-level defaults that are not represented by built actions, or corrupts the option to
action lookup makes the surface incomplete because an action listing alone
would no longer describe what the parser accepts. Delegated global options are
checked against the parent action's parsing and value shape, including
mutually-exclusive group membership and requiredness. Custom converter and
action objects must be identical at runtime; import labels can collide and do
not prove that two parser layers handle a token the same way.

Syntax alone does not say whether `--dry-run` without a selector is meaningful,
what a command will read or change, or whether an output mode is valid during a
refresh. Keep those decisions in a small product-owned TOML review catalog.
The consumer's canonical CLI specification is the readable publication: the
library replaces only a clearly marked generated region containing the live
surface and decision/test table. The adjacent JSON manifest makes parser
changes easy to diff mechanically. The catalog remains human-edited, so reruns
cannot erase rationale, expected effects, or test links.
Sync and check also require the catalog, manifest, and spec to resolve to
distinct files, preventing a destination typo from overwriting the decision
source.

The Markdown view must expose the parser facts a reviewer needs without
opening implementation code: the entrypoint's empty-argv result and each
route's invocation mode. Only the entrypoint and single-command routes have an
empty-argument action to report; a command route parses its remaining tokens,
a route prefix selects a nested command, and a delegated group passes its
remaining tokens to the child CLI. Do not describe every multi-command leaf as
showing help when invoked without a remainder: the leaf parser may dispatch or
reject based on its required syntax.

The generated route inventory also records
mutation and confirmation policy, parser settings, delegated command metadata,
callback inventory and opaque fields;
for each argument and option, its description, token shape, argparse action,
converter, const, choices, defaults, exclusive-group rule, placement, and
hidden status. This keeps the canonical spec useful to an operator reviewing
the whole call surface while the JSON manifest remains the stable input for
diffs and tools.

Argparse converts a token before comparing it with an action's choices. The
review checker models only the exact built-in `str`, `int`, `float`, and
`bool` converters recorded in the surface; it never executes consumer-defined
converter code. This keeps ordinary typed choices mechanically checkable
without running arbitrary product code during a static review. Custom
converters and custom actions remain opaque and their accepted values must be
proven by linked tests that call the real CLI. Any non-default parser
type-registry mapping makes the surface incomplete because argparse resolves
registered types by dictionary equality. Checking only key identity could
miss a distinct key that compares equal to an action's type and changes its
converter. Choice enumeration supports only exact built-in list, tuple, set,
and frozenset containers with exact built-in scalar members. Container
subclasses and custom collections may override membership, so they are marked
incomplete along with non-scalar choice objects whose `.value` or display text
could change argparse's equality result. Argparse does not check choices for a
flag-only action, so the surface marks that registration incomplete. Consumers
can expose the actual command-line scalar choices when their handler maps
those strings to richer internal types.

A non-callable `type` reference on a value-taking action is also incomplete.
Argparse accepts a string type name only when the parser's type registry
resolves it; the exporter marks any non-default registry incomplete because
its converter behavior cannot be safely inferred from the action alone.

Whether the top-level executable shows help before parsing or passes empty
argv to the single-command parser is part of the call contract. Parsing may
still reject required syntax; the surface records the mode without claiming
the handler always runs. This can change while parser actions stay the same,
so the surface shows it and includes it in review signatures.

Argparse's negative-number rule also affects whether a token such as `-1` is
an option value or a positional argument. The exporter records that rule and
the parser's negative-number-like options, and the checker uses those live
values. A customized or uninspectable rule makes the inventory incomplete
instead of guessing from the token's spelling.

The candidate generator is deliberately bounded and symbolic. It covers
minimum syntax, positional shapes and choices, option aliases and choices,
mutually exclusive alternatives/conflicts, parser subcommand aliases, and
explicit product interaction groups. It does not fabricate resource names,
predict acceptance, or try every subset of every option. Option names and help
copy are not evidence for behavior. Relevant syntax changes alter candidate
signatures and request a new decision; removed records stay stale until an
owner explicitly retires them with a reason. Every candidate signature also
includes the route's required positional, option, and exclusive-group
baseline. That makes a change to an action needed to reach the reviewed
dimension trigger re-review, and the checker requires the baseline on every
candidate kind. A required group needs one selected member; repeats of that
member remain valid when the parser accepts them, with product-specific repeat
semantics recorded as explicit interactions. It also reports bare positional tokens that do not map to a
declared action, so unrelated extra values cannot hide an invocation that
would fail before reaching the reviewed option.

An interaction may name an option owned by a different route. This lets the
consumer record both the target command and the foreign option shape in one
stable case, such as passing watch-only `--poll` to `show`. The checker requires
each named target option and the target's required baseline syntax, and it
requires the foreign option to remain unknown on the target parser. It checks
declared value counts and enumerable choices on every occurrence; a valid first
`--tag` cannot hide a malformed second occurrence. For a foreign option,
option-like tokens mark value boundaries, and a flag-only option cannot carry
an inline value. Choice checks model exact built-in conversions; custom
converters and handlers remain the consumer behavior test's responsibility.
That test proves the declared outcome and exact value or repetition rules.
The consumer owns the outcome; the checker does not infer it from route
ownership. For example, `show --poll` should be recorded as a
refusal when the product's `show` route rejects the watch-only option. The
checker also refuses undeclared unknown options so a typo cannot be folded into
the same reviewed case.

The generator does not guess whether a product cares about an optional value
being present or about an option being repeated. Consumers declare separate
named interactions for distinct forms such as `--color` versus
`--color VALUE`, or one `--tag` versus two `--tag` occurrences. The catalog and
generated spec retain those exact argv examples and test links; check mode
validates route, option presence, declared value counts, and enumerable choices,
while the behavior test asserts converter behavior and exact values and
repetition rules. This keeps generic enumeration bounded and makes the product's
reason for testing each form explicit.

When a route or option named by an interaction is removed, sync must still let
the adopter review the new grammar. It omits only that unresolved generated
candidate, lists the stale catalog reference, and keeps the previous semantic
row visible as stale; it does not rewrite the TOML. The owner can update the
interaction and case or explicitly retire that case. This makes regeneration
useful during a breaking CLI change without converting lost references into
lost product decisions.

The shared sync/check/template command means adopters do not implement parser
walkers, a candidate enumerator, a Markdown table renderer, or merge logic.
The companion pytest assertion confirms that active catalog records point to
collected node IDs carrying their `cli_case` markers. That check proves
collection/linkage only. The product test must still invoke its real registered
CLI and assert output, exit status, validation timing, and filesystem/state/
network/credential effects; the normal gate proves that test passes.
The catalog checker only sanity-checks argv structure, including recognized
option arity and scope, parser-depth `--` terminators, and parent positionals
before nested commands. For positional candidates, it assigns supplied values
to the named action at the matching parser depth; another positional using the
same text does not satisfy it. It also rejects an inline value on a flag-only
option. It records `allow_abbrev` at each parser depth and uses that parser's
setting when recognizing options. A callback that changes `prefix_chars` or
enables `fromfile_prefix_chars` makes the inventory incomplete until the
structural checker can model that token syntax. It deliberately does not call
`parse_args`, custom converters/actions, or handlers, because a documentation
check must not execute consumer behavior. The linked test is the oracle for
parser acceptance and product semantics.
Each generated file is replaced atomically. A stop between the two output
replacements is visible as drift on the next check.
This boundary makes semantic review auditable without asking a generic library
to invent product truth.

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

Assay's hard mutation budget is 150 minutes. A registered 60-minute campaign
on the earlier committed revision recorded 490 of 919 candidates before the
hard cap and archived its partial state; it did not produce a mutation verdict.
The longer budget is based on that measured throughput and preserves enough
headroom to finish the full campaign. The combined gate budget is 180 minutes
to include lane startup and the R0/R1 and R3 steps.

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
