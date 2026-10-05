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
consumers. `OptionSpec.hidden` keeps internal options parseable while hiding
them from operator help. The generated semantic surface still includes and
labels hidden options so the product review can account for them.

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

Some consumers declare their registry in a hyphenated, single-file script
instead of an importable package module. Let `surface_cli` load that file
directly, add its parent directory so sibling imports work as they do for a
direct script invocation, and expose the resulting `build_cli()` without an
adapter module. The generated module name must depend on the script name, not
the checkout's absolute path: custom converter labels are part of the manifest
and should not change when a worktree moves. Register the module before
execution so `dataclasses` and other runtime introspection see a normal module.
This is a factory-loading convenience; the Python registry remains the one
grammar definition.

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
hidden status. Optional-value options also record whether this stock argparse
runtime checks an omitted `const` against `choices`. The exporter probes
separate representative constants for `None`, `bool`, `float`, `int`, and
`str`, then records the result for the action's exact built-in const type in the
generated spec and candidate signature. A runtime change is visible for
review. Route invocation-mode flags are required manifest facts: rendering
refuses missing flags instead of silently claiming the route uses the false
case. This keeps the canonical spec useful to an operator reviewing
the whole call surface while the JSON manifest remains the stable input for
diffs and tools.

Argparse converts a token before comparing it with an action's choices. The
review checker models only the exact built-in `str`, `int`, `float`, and
`bool` converters recorded in the surface; it reports known conversion
failures even when no choices are declared and never executes consumer-defined
converter code. This keeps ordinary typed choices mechanically checkable
without running arbitrary product code during a static review. Built-in
converters are recognized by runtime object identity rather than import label,
since a custom callable can expose a colliding label. Custom
converters and custom actions remain opaque and their accepted values must be
proven by linked tests that call the real CLI. Stock choice-checking actions
are recognized by exact class identity so a custom class cannot borrow a
built-in label. Any non-default parser
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
An omitted value for an option with `nargs="?"` selects `const`. Argparse
converts it only when it is a string. Whether the runtime then checks
`choices` is probed and recorded rather than assumed from a Python version.
The checker follows that result without running consumer code. An omitted
optional positional uses its default rather than the option's `const`; the
checker does not apply option-const rules to positional arguments. A
non-scalar option constant makes the surface incomplete because its value
cannot be represented safely.
Argparse checks choices only for the first converted value of
`nargs=argparse.PARSER` and skips choice membership for `nargs=argparse.REMAINDER`.
The surface marks choices declared on a remainder action incomplete so they
cannot look like a parser-enforced restriction.

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

### Version the library's own controls, don't sign them

The library's common controls (`--log-level`, `--quiet`, `--color`, ...) are
not product decisions, yet the first generator signed them into every
consumer's candidates. A copy edit to a library help string, or a new library
control, then changed signatures in every adopting repository at once; each
consumer had to re-review something it never decided (decision CX-D5).

The manifest now records only a per-route list of enabled control names and one
`library_contract` version, and consumer signatures cover consumer-declared
grammar alone. Library releases that do not change a control's syntax or
meaning leave every consumer byte-identical. When one does, the library bumps
`CONTRACT_VERSION` and `check` reports a single finding that points at the
contract notes; the consumer reads, re-syncs, and reviews only genuine
signature changes.

Rejected alternative: re-sign everything on any library change. It is simple
and makes drift impossible to miss, but it turns every library release into a
repository-wide review of unrelated text, which trains reviewers to approve
signature changes unread. The version number keeps the "something you must read
changed" signal rare and meaningful.

Ownership is decided by the action's creation (a marker set in
`add_common_options`), never by flag spelling, so a product option that happens
to be called `--json` remains product grammar. The control table used to check
invocations is derived from the same function with every `include_*` option
enabled, so the contract list cannot drift from the parser. See
[Library contract and contract version](../SPEC.md#library-contract-and-contract-version).

## Declare option constraints structurally

Adopting CLIs kept re-implementing the same post-parse checks in handlers
("`--dry-run` needs a mode", "`--refresh` is not JSON"). Each copy chose its own
wording, ran after some setup, and was invisible to help, the reference and the
review catalog. Declaring the rule on the verb makes the registry refuse it
first, with one wording, before any runtime exists, and lets help, Markdown
and the surface list it. See [Declared option constraints](../SPEC.md#declared-option-constraints).

**Presence is "the parsed value differs from the default".** argparse does not
record whether the user typed an option, so the only portable signal is the
value. That signal is reliable only when the default is `None`, `False` or an
empty list or tuple; for a defaulted option such as `--retries 3`, "present"
would silently mean "different from 3". Rather than guess, `build()` refuses a
constraint that references such an option. The failure is at registration, in
the developer's first run, not as a rule that never fires in production.
Rejected alternatives: tracking the raw argv tokens (breaks abbreviations,
`--opt=value`, and options repeated before and after the verb) and a
custom `Namespace` that records assignment (breaks consumer parsers and
delegates, and changes what handlers receive).

**Constraints stay structural.** Only relationships between options of one
verb are expressible: requires, conflicts, and "requires this value of
that option". A rule that needs loaded configuration, runtime state, a
filesystem check, or domain data stays in the handler, where it can read them.
A general predicate or expression DSL was rejected: it would be a second
grammar to review, and the existing `validate=` callbacks that already carry
such logic would simply move into it.

**The checker never evaluates constraints.** A constraint candidate's case is
an invocation that usually breaks the rule on purpose, and the structural
checker only proves that an invocation parses. Whether the product then
refuses it, with which status, is a catalog decision backed by a real-CLI
test, the same as for exclusive groups. Evaluating constraints in the checker
would make the manifest a second implementation that could drift from the
runtime. Constraints do participate in signatures: they are consumer-declared
grammar, so changing a rule or its reason asks for re-review.
Library controls referenced by a constraint are named by their canonical flag
only, so a library syntax change never moves a consumer signature.

### Selector lists as a value type

`all`, one name, or `a,b` appears in several adopting CLIs, each with its own
parser and its own error wording. `SelectorList` is an argparse `type`, so a bad
value is an ordinary usage error, the surface records its `choices`, `all_token`
and `separator` exactly (it is not opaque), and the checker applies the same
rules to a catalog invocation. It deliberately returns structure only: the
given-order tuple, or the `SelectorList.ALL` sentinel when the full set is only
known at run time. Resolving names against loaded data, ordering, and defaults
stay in the consumer.

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

## Keep consumer tests hermetic and the plugin opt-in

Every adopter had copied the same `_invoke`, and each copy decided differently
what to scrub. The shared helper makes `home` a required keyword rather than a
default: the 2026-10-04 run-gate test that leaked a fake `assay` into the real
`~/.local/bin` is what a convenient default produces. It also prepends the
imported library's directory to `PYTHONPATH` so the child runs the revision
under test, not whatever is installed. Rejected: defaulting `home` to a
temporary directory, which hides the isolation decision and makes failures
harder to inspect.

The pytest plugin is not a `pytest11` entry point. Entry points load into every
pytest run in the environment, so merely installing the library would change
collection for unrelated projects; one `pytest_plugins` line is a visible,
reviewable opt-in. It is strict by default because a silently uncollected case
test is exactly the drift the catalog exists to catch; `--cli-case-partial` is
the explicit, local-only escape for running one file, and it still enforces
every error about a test that was collected.

## Make long operations automation-safe

Progress is a presentation policy, not the operation's result. `auto` chooses
interactive redraw only for a TTY and plain newline events otherwise;
`rawjson` is explicit JSONL for a machine consumer. The renderer does not
start threads, own a signal handler, or decide when an operation is complete:
the owning command retains control of retries, cancellation, and state
transitions.

## Version sources must agree and failures must be explicit

`CliIdentity.resolve()` accepts an installed distribution and a checked-in
version file together because a source checkout and an installed wheel can
silently drift: whichever source happens to be read first would win and the
other would be quietly wrong. Requiring agreement turns that drift into an
immediate `VersionLookupError` at startup. A missing source is "unresolved",
but a malformed version file is an error, since treating garbage as absent
would let the other source mask a broken release. A literal fallback version
was rejected for the same reason `from_distribution` has none.

`unexpected_exceptions` defaults to `"raise"` because changing what an
existing consumer does with an uncaught bug is a behavior change that the
consumer should choose explicitly. The adoption audit recommends `"report"`:
operators see one line and an exit status of `1` instead of a stack, and
`--traceback` restores the original exception for whoever is debugging. The
option exists only in `"report"` mode so that `"raise"` consumers see no
interface change, and a consumer-declared `--traceback` is refused so two
options cannot share one spelling with different meanings.

`--dry-run` is hooked into `runtime.confirm()` rather than being a flag each
handler must interpret. Handlers already gate a mutation on consent, so making
consent answer "no" while dry-running makes every existing handler safe
without new branches, and a forgotten `if args.dry_run` cannot mutate. The
cost is that a handler wanting a preview must print it before calling
`confirm()`. `--yes` deliberately does not override it: previewing is the
stronger statement. It is only valid on mutating verbs because a read-only
verb has nothing to preview, and a consumer's own `--dry-run` is refused once
a verb opts in so the two meanings cannot diverge.

## Review the surface with the agent harness and keep findings separate

Judging whether a help text is clear or a verb needs a dry-run is a language
task, and the library is stdlib-only with no model access. So the library
collects the evidence deterministically (`surface pack`: rubric, every route's
plain help, every case awaiting review) and the agent harness, which already
has a model and a conversation, does the judging. The alternative of calling a
model from inside `cli-extended` was rejected: it would add a network
dependency, a credential, and nondeterminism to a gate command.

The judgment lands in two hand-edited files. The review catalog records
per-case decisions keyed to stable surface IDs. The findings file records
what is wrong, with a remedy, and survives the case being accepted: a case can
be correctly refused while a finding about its help text stays open. Keeping
them apart lets `check` treat them differently. An open `blocker` or `major`
finding fails `check`; `minor` and `note` findings are reported but do not,
because a gate that fails on every style remark gets switched off. Findings
that name a route which no longer exists fail as stale, so closed-out work
cannot silently point at nothing.

`surface sync`, `check` and `template` are deliberately not marked `mutating`.
The exemption criterion: they write only generated, idempotent files the
library owns (the manifest and the marked spec region), re-running them
changes nothing, and they never touch the catalog or findings. A confirmation
or `--dry-run` there would only get in the way of `check`-style gating.
Any verb that changes state it does not own or cannot regenerate still needs
`mutating` with a confirmation or dry-run.

The library never rewrites the catalog or the findings file. A tool that
rewrites reviewed text turns every regeneration into a diff nobody wrote and
loses comments, ordering and the author's wording; hand edits by the reviewer,
checked by the library, keep the record attributable. `sync` therefore
renders open findings into the marked spec region (read-only echo) but never
the other way round.

## Ship agent skills per tool and stamp them

Each tool packages its own skills instead of a central repository syncing them
into every harness. A per-tool package keeps the skill version equal to the
installed tool version, and a consumer-only checkout needs neither a vbpub
clone nor symlinks. The cost is one explicit `skills install` step, because a
wheel cannot run post-install hooks.

An installed copy carries three markers. The `metadata` keys in the frontmatter
and a visible banner tell a human or an agent reading the file which tool and
version wrote it and how to check for drift. They are not trusted, though: the
sidecar `.cli-extended-stamp.json` records a hash per installed file, and that
is what separates `modified` (the user edited it) from `stale` (the tool moved
on). Putting the integrity record outside `SKILL.md` means editing the file
cannot silently rewrite its own evidence.

`foreign` and `unmanaged` directories are never overwritten, not even with
`--overwrite-modified`. Two tools that ship a skill with the same name would
otherwise take turns destroying each other, and a hand-written skill with a
colliding name is exactly what a user would be upset to lose. Refusing is
cheap; the owner can remove the directory deliberately. `--overwrite-modified`
is a dedicated flag rather than `--yes` because `--yes` is generic consent
to a prompt and must not also mean "discard my local edits".

The source schema is deliberately a subset of YAML (single-line scalars plus
one `metadata:` block) so the library can validate and extend the frontmatter
without a YAML parser, which keeps the runtime dependency-free. Folded and
literal scalars are rejected with a line number instead of being guessed at.
`--harness all` is the default (dstdns D-647 #6) because most operators run
both Claude Code and an `~/.agents` harness and a skill that exists for only
one of them is the surprising case; `--dest` covers everything else.
Installation writes a temporary sibling and renames it into place, so a crash
never leaves a half-written skill, and `check`/`list` read the same state
machine so the answer cannot differ between the verbs.

## Audit mechanically, judge with a skill

`cli-extended audit` only reports what a program can decide: whether a surface
is configured and current, whether a library control is shadowed, whether a
dependency is declared. It never says a CLI is well designed. Anything that
needs a decision, such as whether a `configure` callback could be declarative or
whether a mutating verb really needs no dry-run, is `manual`, and the packaged
`cli-extended-adoption` skill hands those to an agent that records each judgement
as an `adoption` finding. The alternative, an audit that guesses with ever more
elaborate rules, produces confident wrong answers; a `pass` must mean something.

The few checks that read project source text (version reader, dependency
declaration, path hacks, plugin enablement) are plain substring scans and say so
with a `heuristic:` prefix, name the files they matched, and stay deliberately
small. A false positive costs one finding with a `wontfix` rationale; a clever
parser would cost a maintenance burden and still be wrong sometimes. The scan
skips virtualenvs, build output and `.worktrees`, and exempts `run-gate.toml`
from the path check because gate lanes legitimately point at the tested source.

Every check maps to exactly one stable checklist row (`AC-NN`), and a test keeps
the document and the code in step, so a feature cannot be audited without being
documented or the reverse. A `warn` marks a policy the library recommends but a
tool may keep on purpose (`unexpected_exceptions="raise"`); only a `fail` sets
exit status 1, so CI can gate on the unambiguous problems and let judgement items
flow through findings.

## One doctor verb; crashes are failures

Every tool grew its own `doctor` with its own output and exit rule, so
operators and CI could not rely on any of them. The shared verb fixes only the
shell contract: named checks, four statuses, one text line per check, a JSON
shape, and exit 1 exactly when something failed. What a check inspects stays
the consumer's decision.

A check that raises is reported as `fail` (`check crashed: <Type>: <message>`)
and the remaining checks still run. The alternative, letting the exception end
the run, hides every later check behind the first bug; swallowing it as `ok` or
`skip` would make a broken probe look healthy, which is the one thing a doctor
must never do. `KeyboardInterrupt` still propagates. Non-JSON `details` are
also a `fail` instead of a crash of the whole report, since by then the other
results are already known.

The `skills` check is automatic because "installed skills are current" is the
same question for every tool and `skills check` already defines it; a doctor
that forgot it would pass while the agent runs a stale skill. It is resolved at
run time because the two registration calls are independent and a consumer
should not need to know which must come first. A consumer cannot reuse the name
`skills`, so the built-in meaning is never ambiguous. Skills that were never
installed are a `warn`, not a `fail`: a tool whose skills were never installed
is not broken, whereas a stale, modified, foreign or orphaned install, or an
interrupted-install leftover, is. Warnings and skips do not
fail the run: they are for advice and inapplicable checks, and failing CI on
them would train people to ignore the verb.

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
