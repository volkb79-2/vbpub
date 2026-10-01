# cli-extended specification

This document is the normative specification for `cli-extended`. It defines
the common command-line contract for user-facing Python CLIs in this
repository. It applies to installed commands, repository scripts, and
executable Python entrypoints. A tool may add domain-specific behavior, but
must not silently contradict this contract. If a compatibility requirement
requires an exception, the exception is documented in that tool's own guide
and tested at its boundary.

The standard is deliberately about observable behavior: what an operator sees,
what an automation caller can rely on, and which side effects are permitted
before a command is understood.

## 1. Identity and version

Every CLI has one authoritative version source. It must be derived from the
tool's package metadata, declared version module, or another checked-in source
of truth. A CLI must not invent a fallback version that can silently become
false.

The first line of every human-facing help or usage document is the product's
short name, version, and long name:

```text
CIU 7.15.0 — Container Infrastructure Utility
```

The same identity line appears on command-specific help and argument or
configuration diagnostics when the CLI owns the diagnostic. A command must not
print multiple competing identity lines.

`version` and `--version` are mandatory and are side-effect free. Both print
exactly the short identity and exit successfully:

```text
ciu 7.15.0
```

The long name belongs in help/usage, not in the output of `version` or
`--version`. Version and help must work without a config file, credentials,
network access, Docker, SSH, or other runtime dependencies.

## 2. Invocation and help

The canonical top-level grammar is:

```text
tool <verb> [options]
tool help [verb]
tool version
```

The following forms are required:

```text
tool --help
tool help
tool <verb> --help
tool help <verb>
tool --version
tool version
```

`help <verb>` and `<verb> --help` must render the same command-specific help.
Help is a discovery operation: it must not authenticate, read secrets, make
network calls, mutate files, start services, or otherwise perform the verb's
action.

`--help` is the only documented help option. The short `-h` spelling is not
part of this standard and must not be added to new CLIs. Existing `-h` support
is removed as each CLI is migrated, unless a separately documented external
compatibility promise prevents that removal.

When invoked without a verb, a CLI must print its complete top-level usage to
stdout and exit `0`. It must not let argparse expose an implementation detail
such as “the following arguments are required”. The only exception is a CLI
whose documented purpose is specifically a no-argument action; that action must
still have a separate `--help` path.

Unknown verbs, missing required values, and malformed options print a concise
`[ERROR]` diagnostic as the first block on stderr, followed by a blank line and
the product identity plus relevant help; they exit `2` and must not print a raw
traceback. This makes the failure immediately visible without displacing the
identity heading from the help itself.

Once a known verb has been identified, a command-local invocation error also
prints that verb's complete help immediately. This includes missing required
positional arguments, missing required options, invalid choices, invalid
values, and invalid option combinations. The operator should not have to type
the same command again with `--help` to discover the remedy. The output must
contain the failed-operation diagnostic first, a blank line, and the same
command help available from `tool <verb> --help`, and must still exit `2`.

Every usage synopsis must match the parser's real constraints. In particular,
required mutually exclusive alternatives must be shown as required alternatives
(for example, `(--config FILE | --config-json JSON)`), not as separate optional
flags. Prefer deriving usage from structured argument/option declarations; an
overridden synopsis is exceptional and must remain consistent with parser
validation.

Long options require their complete spelling. `CliRegistry` disables argparse's
prefix abbreviation by default on the root parser and every verb parser, so a
retired spelling such as `--to` cannot be accepted accidentally as an alias for
`--token`. A consumer may opt in with `CliRegistry(..., allow_abbrev=True)` only
when prefix matching is an intentional part of its public grammar and is tested.

An error before a known verb can be identified prints the top-level help. A
parser must not let argument ordering hide a more useful command-local error;
for example, `tool extract` should explain that its session log is missing and
show `extract` help, rather than only printing a generic parser synopsis.

## 3. Top-level usage document

The top-level usage document is one coherent document, not a parser-generated
verb list followed by a second handwritten list. A tool may render it from
structured verb metadata, but every public verb must appear exactly once.

The recommended layout is:

```text
TOOL 1.2.3 — Long Tool Name

Usage: tool <verb> [options]
       tool help [verb]
       tool version

One-sentence description of the overall CLI and the job it performs.

GETTING STARTED
  ...the shortest useful workflow...

EXPLORATION
  ...read-only verbs...

MODIFICATION
  ...verbs whose default operation changes state...

MIXED OPERATIONS
  ...verbs with both read-only and mutating actions...

AUTHENTICATION / SETUP
  ...login, init, configure, or prerequisite verbs...

MAINTENANCE
  ...cleanup, repair, migration, or administrative verbs...

GLOBAL OPTIONS
  --help       show this help and exit
  --version    print the short version and exit
  --debug      show additional diagnostic information
```

The sections are semantic, not implementation layers. A small CLI may omit
empty sections. A larger CLI should use groups when they materially improve
discovery. Group order should follow the operator's likely workflow, with
alphabetical order inside a group unless a documented workflow order is more
useful.

Top-level entries list the verb name only, followed by a concise description.
The description column is calculated once across all semantic groups so every
entry starts at the same horizontal position. Positional arguments, options,
and alternative syntax belong in that verb's detailed `--help` output, not in
the catalog line. For example:

```text
  status       show installation state and recent logs
  configure    create or update validated settings [interactive]
  install      run the two-stage installation [mutating; potentially expensive]
```

Top-level entries should say whether they are read-only, mutating, interactive,
or potentially expensive. A mixed verb appears once under `MIXED OPERATIONS`;
its concise summary identifies that it also has mutating actions. Detailed
help for `firewall` can show `firewall SERVER [MAC] get|set` and explain which
action is confirmed.

Nested actions are actions, not top-level verbs and not boolean options. They
are written as positional action names (`snapshots SERVER create`), not as
misleading flags (`snapshots SERVER --create`).

The same structured verb metadata may also render a Markdown usage document
for a README or operator guide. Markdown is a documentation format, not a
second hand-maintained command list: it must be generated from the same
metadata used by terminal help. Terminal `--help` remains plain text unless a
CLI explicitly documents another format.

The preferred implementation is one command registry that generates parser
registration, grouped help, help lookup, and dispatch. Each verb definition
owns its synopsis, description, examples, semantic group, and behavior
attributes. Positional arguments and options carry their own names, help,
argparse constraints, and help-group placement. Do not repeat the verb list in
a parser, handler map, and handwritten usage block when a registry can derive
those surfaces. Custom parser callbacks are an escape hatch for genuinely
nested or conditional argument structures.
`CliRegistry` also configures exact option matching across the root and child
parsers; `allow_abbrev=True` is an explicit, exceptional opt-in.

## 4. Getting Started and examples

A `GETTING STARTED` section is included when a first-time operator benefits
from an on-ramp. It should contain the smallest safe sequence that gets useful
output, for example authentication followed by a read-only status command.

Every verb with non-obvious behavior has at least one pasteable example in its
command-specific help. Every mutating verb has:

- a read-only inspection or dry-run example when one exists;
- an explicit confirmation example using the normal prompt; and
- a `--yes` example only where unattended acceptance is a supported use case.

Examples must use valid argument names and current closed-vocabulary values.
They must not contain fake defaults that the implementation does not actually
derive or accept.

## 5. Common options

Common options are shown in semantic sections rather than one undifferentiated
list. A CLI only advertises options that apply to the selected command.

### Help and version

```text
help
--help
version
--version
```

### Debugging

```text
--debug    emit additional diagnostic information useful for debugging
--debug-raw emit diagnostic/API data without secret redaction (dangerous)
```

`--debug` may enable request details, retry decisions, subprocess commands,
timing, or other diagnostic context, subject to the tool's secret-redaction
policy. It must not turn a handled failure into a raw traceback, turn a
successful operation into a failure, or bypass normal safety checks. Sensitive
values remain redacted. An unexpected exception may retain its traceback
because it is uncaught; `--debug` may add context around it but does not change
that distinction.

`--debug-raw` is an explicit opt-in escape hatch for diagnosing a provider or
subprocess response whose secret-bearing fields are themselves relevant. It
implies `--debug`, disables presentation redaction for supported diagnostic and
raw-response paths, and must not change the request or mutation being made.
The CLI prints a prominent warning to stderr before emitting raw data. The
warning must say that credentials, tokens, passwords, private keys, or other
secrets may be exposed and must not be copied into shared logs. Raw mode must
remain opt-in per invocation; it must not be enabled by a persistent default,
ordinary debug environment variable, or a config-file default.

### Severity and verbosity

Human-readable diagnostic and progress messages use a small, stable severity
vocabulary:

```text
[INFO]  normal progress or an informative result
[WARN]  the operation can continue, but needs attention
[ERROR] the requested operation failed or was refused
[DEBUG] additional diagnostic detail
```

The severity tag remains present when colour is disabled or output is
redirected. Colour is only presentation: a typical palette is dim/cyan for
`INFO`, yellow for `WARN`, red for `ERROR`, and dim for `DEBUG`. A tool may
choose a different accessible palette, but must not communicate severity by
colour alone.

Actionable follow-up is a hint, not a severity. It is rendered as a separate
line such as `Hint: run tool login first` or `Next: inspect tool status`, and
must prescribe a factually valid remedy rather than inventing a default.
Hints are omitted from machine-readable stdout and may be represented as
structured fields when the command has a JSON schema.

The canonical verbosity control is:

```text
--log-level {error,warn,info,debug}
```

The default is `info`. `--quiet` suppresses informational/debug output while
retaining warnings and errors (equivalent to `--log-level=warn`), and `--debug`
is an alias for `--log-level=debug`. `--debug-raw` implies debug verbosity in
addition to disabling supported redaction. The level controls
diagnostic/progress messages, not the command's primary result: a successful
query still returns its result at `--quiet`, while warnings and errors remain
visible. `--verbose` is accepted by the common helper as a compatibility alias
for debug. New interfaces should not invent a numeric `--verbose` scale
alongside `--log-level`.

The level options are mutually exclusive. If more than one is supplied, the
CLI reports the conflict and prints the relevant help rather than silently
using argument order as policy.

### Output control

Where supported, output options are grouped together:

```text
--json       emit machine-readable output
--color      force terminal colour where supported
--no-color   disable terminal colour
```

Human-readable output is for operators. JSON output is for consumers and must
contain no banners, progress text, warnings, or diagnostics on stdout. Errors
and diagnostics always go to stderr. If a JSON schema is versioned, the JSON
document carries its schema version.

### Confirmation

```text
--yes        accept confirmation prompts without waiting for input
```

`--yes` is valid for commands that can ask for confirmation. It pre-defaults
user acceptance; it does not bypass validation, authentication, protected
target deny-lists, explicit target requirements, dry-run rules, or other safety
guards. A command must still refuse an unsafe or incomplete request with a
meaningful diagnostic.

The common registration should expose `--yes` only for verbs marked as
mutating (or a specifically documented mixed verb that has a mutating action).
`VerbSpec.confirmation_required` controls this independently when needed:
`None` preserves the default of requiring confirmation for mutating verbs;
`False` suppresses `--yes` and the generic confirmation wording while keeping
the mutation label; and `True` requires confirmation and is valid only for a
mutating verb. The older `include_confirmation` spelling remains a compatible
alias; a declaration must not set both fields to conflicting values.
The shared confirmation helper may own default-no prompting, TTY refusal, and
EOF handling, but the CLI owner must validate the exact target/change first
and call confirmation immediately before making that change.

The normal interactive prompt must clearly state what will change. A declined
prompt and an intentional Ctrl-C are clean cancellations, not tracebacks.
When stdin is not interactive, a command must not attempt a prompt that will
fail with EOF. It must either have a documented non-interactive default or
refuse with an actionable message naming `--yes` or the required input. EOF is
handled as a clean refusal/cancellation, never as an uncaught traceback.

### Interactive data collection

`CliRuntime.prompts` provides shared collection mechanics for multi-step
interactive flows. The API is optional and does not validate domain schemas,
write files, persist defaults, or decide whether a collected value is safe to
apply:

```python
runtime.prompts.text(message, *, default=None, required=True) -> str
runtime.prompts.password(message, *, required=True) -> str
runtime.prompts.confirm(message, *, default=False) -> bool
runtime.prompts.select(message, choices, *, default=None) -> str
runtime.prompts.checkbox(message, choices, *, default=()) -> list[str]
```

Choice lists are non-empty sequences of unique strings. A selection must be a
member of its declared choices; checkbox results contain only unique declared
values. Required text rejects whitespace-only answers. Optional text accepts
the driver's supplied default or an empty string. A required password rejects
an empty answer; password contents are never printed by the prompt UI and are
registered for ordinary output redaction.

The default driver uses the optional `questionary` package from the
`interactive` extra. Import it only when a prompt is actually requested;
help, version, imports, and non-interactive commands must work without that
extra. Before importing or calling any driver, the runtime verifies that both
injected stdin and stdout are TTYs. A caller may inject a `PromptDriver` through
`RegisteredCli.run(prompt_driver=...)` for tests or another UI, but the same
TTY rule still applies. Consumers that vendor `cli-extended` may set
`interactive_extra="product[interactive]"` so a missing-dependency hint names
their own package extra.

An Escape/cancel result raises `PromptCancelled`; it is not converted into an
empty, false, or partial answer. `RegisteredCli.run()` handles an uncaught
`PromptCancelled` as a clean cancellation with status `130`. Questionary's
Ctrl-C is allowed to remain `KeyboardInterrupt`, which the same CLI boundary
handles with status `130`. Prompt drivers return values only: callers retain
domain validation, confirmation policy, and persistence.

Other recurring option groups should be named according to meaning, for
example:

- `target selection`: server, project, host, or resource selectors;
- `filters`: query, state, time range, pagination, or name filters;
- `input`: file, JSON, config, or stdin sources;
- `stop conditions`: timeout, polling, retry, or completion conditions;
- `authentication`: credential, identity, or trust inputs;
- `modification`: fields that replace, create, delete, or otherwise change state.

Global options may be accepted before the verb. A tool may also accept them
after the verb for ergonomics, but the accepted placement must be consistent
within that CLI and documented in its help. When a shared option is accepted
on both sides of a verb, validation must span the whole invocation: mutually
exclusive controls such as `--quiet` and `--debug` must not become
last-one-wins merely because they were parsed by different command levels.

## 6. Errors, exceptions, and cancellation

The CLI distinguishes expected operator errors from programming failures.

- Expected validation, configuration, authentication, API, filesystem, and
  subprocess failures are caught at the CLI boundary and rendered as concise,
  meaningful messages that state the failed operation and, where possible, a
  corrective action.
- Expected failures do not print Python tracebacks.
- `KeyboardInterrupt` is always handled as intentional cancellation. It prints
  a short cancellation message, never a traceback, and exits `130`.
- `PromptCancelled` is a clean Escape/question cancellation and exits `130`
  when it reaches the shared CLI boundary; a consumer may catch it to perform
  its own cancellation cleanup.
- An exception that is not handled by the CLI boundary is an unexpected
  programming or environment failure and may print its traceback to stderr.
  This distinction must not be hidden by a broad `except Exception` that turns
  every bug into an uninformative message.
- `--debug` may add diagnostic context for handled failures, but normal
  operation still uses the same meaningful error and exit status. It does not
  authorize raw tracebacks for exceptions the CLI has deliberately handled.

Error messages must not claim a stronger conclusion than the check performed.
For example, “not reachable” must not be rendered as “no keys match”, and a
failed API lookup must not be rendered as an empty resource list.

## 7. Exit-status contract

Unless a tool documents a stricter domain-specific status, the common meanings
are:

| Status | Meaning |
|---:|---|
| `0` | requested operation completed, or help/version was printed |
| `1` | expected runtime failure: API, filesystem, subprocess, or external state |
| `2` | invocation, validation, configuration, or safety refusal |
| `130` | operator cancelled with Ctrl-C |

For a registered command, a handler may return `None` to indicate success;
`RegisteredCli.run()` normalizes this to process status `0`. A handler may
return an integer when the product contract defines another status. Consumers
should not repeat `return 0` solely to restate the shared success default.

Pipelines, wrappers, and background launchers must preserve and report the
status of the operation being judged, not the status of a pager, logging pipe,
or wrapper.

## 8. Formatting and terminal behavior

Help and human output must be readable in a wide terminal and remain usable
when redirected. The formatter should derive its width from the terminal and
use a documented wide fallback (recommended: `120` columns), rather than
hard-wrapping all prose at `80` columns. Long option descriptions should wrap
at word boundaries and preserve indentation.

Colours are optional presentation. Automatic colour must be disabled when
the destination stream is not a TTY, when `NO_COLOR` is set, or when the CLI
provides `--no-color`. Generated terminal help follows this policy just like
diagnostics and progress; the automatic check uses stdout for help and stderr
for diagnostics/progress. Semantic meaning must remain available in plain
text. If both explicit colour controls are exposed, `--no-color` disables
automatic colour and `--color` may explicitly override `NO_COLOR`; JSON,
version output, and primary result data never use ANSI colour. Supplying both
explicit controls is an invocation error rather than a last-option-wins rule.

`CliOutput.color_enabled(stream)` is the public stream-specific query for a
consumer-owned renderer. With no explicit switch it reports TTY plus
`NO_COLOR`; `--color` forces true and `--no-color` forces false. The result
does not override the plain-output requirement: a consumer that emits JSON or
another machine-readable result must keep it free of ANSI, including when
`--color` was supplied. Query the actual destination stream because stdout
and stderr can have different terminal states.

### Progress

Long-running verbs that provide progress should expose the following modes:

```text
--progress {auto,tty,plain,quiet,rawjson}
```

`auto` uses an interactive spinner/redraw only for a TTY and uses one
newline-delimited event per update otherwise. `tty` requests interactive
terminal presentation, `plain` requests stable newline-delimited text,
`quiet` suppresses progress while retaining the final result and diagnostics,
and `rawjson` emits newline-delimited structured progress events without
colour or terminal control sequences. Human progress goes to stderr so that
normal stdout remains a result stream; `rawjson` is a machine mode and owns
stdout for the progress event stream. A command must document whether it
combines a final result with `rawjson` events or represents completion as a
final event. When `--json` selects the command's primary stdout result,
`--progress=rawjson` is valid but deliberately muted; use `plain` or `tty` if
progress is wanted on stderr. This prevents a primary JSON document from
being mixed with a second machine stream.

Tables must preserve stable column meaning, sanitize external values before
printing, and use explicit placeholders for unknown values. “Could not check”
must remain distinct from “empty” or “not present”.

## 9. Implementation and test requirements

Each CLI should centralize these concerns in a small shared helper or base
parser rather than reimplementing identity, version, errors, and width rules in
each verb.

The test suite for every adopted CLI must prove at least:

1. no arguments print complete help, perform no side effects, and exit `0`;
2. `--help`, `help`, `verb --help`, and `help verb` behave as specified;
3. `version` and `--version` print the exact short version and perform no side
   effects;
4. every public verb appears exactly once in the grouped top-level help;
5. no top-level help output contains an ungrouped duplicate parser verb list;
6. `--yes` suppresses only the intended confirmation prompt;
7. expected failures are meaningful and traceback-free;
8. Ctrl-C at every interactive prompt exits `130` without a traceback;
9. prompt collection validates its declared types/choices, distinguishes
   cancellation from valid false/empty answers, refuses before driver use when
   either injected terminal stream is not a TTY, and imports its optional
   dependency only on a real prompt request;
10. unexpected exceptions remain distinguishable from handled failures, with
   `--debug` providing additional diagnostics and `--debug-raw` explicitly
   proving the redaction boundary is disabled only when requested;
11. severity tags, hints, colour/`NO_COLOR`, and each supported log level
    remain meaningful when output is redirected;
12. JSON mode keeps stdout machine-readable and diagnostics on stderr; and
13. help/version paths work without credentials, configuration, network, or
    runtime services.

Tests should exercise the real entrypoint or module invocation, not only helper
functions. A controlled bad input must demonstrate that each safety/error
oracle actually goes red before the fix.

Every adoption MUST keep two contracts distinct: the generated grammar
inventory and the product's semantic command inventory. The product's
canonical specification MUST account for every supported leaf verb and
option, including shared/hidden options, accepted values, defaults and
omission, selection scope, valid/refused combinations, effects, dry-run and
confirmation boundaries, and output/status behavior. A registry/help
synchronization test proves only that the declared syntax is current;
behavioral tests must prove the declared effects and refusals.

## 10. Implementation guidance

The standard does not require a particular CLI framework. Python's standard
library is sufficient for the baseline contract:

- `argparse` supplies typed options, subcommands, required values, option
  groups, and version actions;
- a small `ArgumentParser` subclass supplies identity-headed errors, dynamic
  width, and command-help-on-error;
- a thin dispatcher handles `help [verb]`, `version`, and the no-argument
  path before ordinary parsing;
- `importlib.metadata` or a declared version module supplies the authoritative
  version;
- `shutil.get_terminal_size`, `textwrap`, `signal`, `contextlib`, and
  `traceback` cover terminal sizing, cancellation, cleanup, and unexpected
  failures; and
- `dataclasses` or typed dictionaries can hold the one verb/group/option
  metadata table used to generate parser registration, top-level help, command
  help, Markdown docs, and dispatch without parallel hand-maintained lists.

The shared helper is free to reuse established Python libraries where they
materially improve correctness, presentation, terminal handling, structured
output, or testing. Such dependencies must be deliberate and documented; the
helper must wrap them behind this repository's observable contract so a library
upgrade cannot silently change help layout, error semantics, redaction, or exit
statuses. A CLI should not acquire multiple overlapping parser/rendering
frameworks merely because each project chose a different one.

The repository has enough repeated behavior across CIU, CMRU, nyxloom, and the
Netcup tools that a small custom library is worthwhile. It is a focused
contract layer, not a replacement for every possible CLI framework. It may use
or adapt an established parser/renderer, but its narrow API covers:

The recommended home is `libraries/cli-extended/`, with distribution name
`cli-extended` and Python import name `cli_extended`. It must be independently
packageable so installed CLIs do not depend on importing from the repository
root.

1. `CliIdentity` and authoritative version resolution;
2. a declarative verb/argument/option registry that generates parsers, grouped
   help, dispatch, and Markdown usage from one set of metadata;
3. a common parser wrapper for `--help`, `--version`, `--debug`, `--yes`,
   `--debug-raw`, `--log-level`, colour/progress controls, and
   command-help-on-error;
4. severity-tagged diagnostics, hints, TTY-aware colour, and progress
   rendering with a plain fallback;
5. the outer exception and Ctrl-C boundary; and
6. reusable black-box contract-test helpers for help/version, parse errors,
   output streams, exit statuses, and confirmation behavior. Each consumer
   still proves side-effect freedom with its domain-specific API/filesystem
   fakes or state probes.

Domain verbs, API clients, configuration loading, and destructive-operation
policy must remain in each owning project. The helper must not import CIU,
CMRU, nyxloom, Netcup, Docker, or any project-specific configuration. It must
also be packaged or otherwise made available to installed tools; relying on a
repository-root import path would make a standalone CLI work only from this
checkout.

Existing project-local helpers (`ciu.cli_utils`, `cmru.cli_support`, and
nyxloom's parser classes) remain useful migration evidence, but keeping
independent implementations would recreate the drift this standard is
intended to prevent. First-party adopters include the CMRU operator scripts
and active handler adapter, the Debian installer, and the three Netcup
entrypoints. Larger CLIs remain follow-on migrations with compatibility
tests.

## 11. Further contract areas

The following areas should be covered by the standard or explicitly marked
tool-specific before a CLI is considered fully adopted:

- **semantic command contract:** each leaf's purpose, selectors, accepted
  values, defaults/omission, option interactions, effects, confirmation and
  dry-run behavior, and output/status. Keep the full table in the product's
  canonical specification; the shared registry cannot infer domain truth;
- **distributed entrypoints:** installed scripts, active module CLIs,
  project-step/bootstrap adapters, library APIs, and generated standalone
  tools, including which ones are supported from the built wheel;
- **stdin and prompts:** prompt defaults, EOF, non-TTY behavior, and whether
  `--yes` is required or merely optional for each mutation;
- **configuration precedence:** the documented order among command-line
  options, environment, config files, and derived facts, with no silent
  shadowing defaults;
- **secret handling:** redaction in human output, debug output, exceptions,
  JSON, temporary files, and subprocess arguments;
- **output stability:** table column vocabulary, unknown versus unavailable
  values, locale/time-zone assumptions, deterministic ordering, and JSON
  schema/version policy;
- **progress and cancellation:** whether progress uses stderr, how long
  operations report liveness, and how SIGINT/SIGTERM/closed-pipe conditions
  terminate;
- **pagination and limits:** whether a list is complete, paginated, truncated,
  or filtered, and how that fact is disclosed;
- **aliases and deprecation:** compatibility names, warning destination,
  removal policy, and tests that keep aliases from silently changing meaning;
- **pass-through arguments:** an explicit `--` boundary wherever a verb runs a
  child command, so child options cannot be mistaken for CLI options; and
- **completion and automation:** shell completion, stable machine-readable
  output, and whether prompts are forbidden in CI/non-TTY contexts.

## 12. Adoption inventory

This is an adoption inventory, not a claim that all current tools conform.
The current scoped reviews cover CMRU, the Debian installer, and the Netcup
tools; other rows remain future work and were not re-audited here.

| CLI | Main adoption work |
|---|---|
| `debian-install-v2.py` | adopted through `cli-extended` for registry, help/version, common options, output, and dispatch; `bootstrap-remote.py` is the documented stdlib-only bootstrap exception |
| Netcup `scp-api.py`, `install-host.py`, `monitor-task.py` | adopted through `cli-extended` for generated verbs/help, identity/version, common diagnostics, and clean cancellation; Netcup retains API, confirmation, denylist, and install policy |
| `ciu` | align `help` verb and remove `-h`; retain its strong grouped/help model |
| `cmru`, `cmru-agent`, `cmru-controller`, `python -m cmru.handlers` | use the registered grammar and generated help; the CMRU SPEC records the semantic review. The active handler module is a project-step/bootstrap CLI; bundle and runner remain libraries; standalone generated `get.py` intentionally retains argparse. |
| `nyxloom` | make bare invocation exit `0`; remove flat parser list; align version output and `help` |
| other `scripts/` CLIs | audit and adopt the same contract when they are user-facing |

The Debian installer and Netcup tools are the current first-party consumers.
The standard itself is repository-wide; this scoped migration does not claim
or require that every repository CLI is converted at once.

## 13. Generated CLI surface and semantic review

The canonical product CLI specification MUST contain both the current grammar
inventory and the product-owned semantic review for each supported verb and
option. `cli-extended` provides helpers to export the built `RegisteredCli`,
render a bounded review checklist, synchronize a generated Markdown region,
and check generated files and semantic decisions. This process does not
replace product review of the verbs, groupings, option meanings, or effects.

The executable grammar source remains the Python `CliRegistry`. A TOML review
catalog stores human decisions and option-interaction groups; it MUST NOT be
treated as a second parser definition. A generated JSON manifest records the
installed argparse tree after `configure(parser)` callbacks and delegated
registries have been applied. The consumer's canonical CLI specification
embeds generated Markdown between exactly one pair of these standalone lines:

```markdown
<!-- cli-extended-surface:start -->
<!-- cli-extended-surface:end -->
```

The generator MUST update only the manifest and the text between those
markers. It MUST preserve all text and line endings outside the marked region,
and MUST NOT write or reserialize the TOML catalog. Missing, duplicated,
reversed, or nested markers are errors. Sync may write new generated files
while reporting pending decisions so a grammar change is reviewable; check is
read-only and exits unsuccessfully on manifest/spec drift, missing or pending
cases, changed signatures, stale cases without explicit retirement, or
uninspectable syntax.
Each output file MUST be replaced atomically so interruption cannot leave a
partially-written manifest or spec. The two replacements are independently
atomic; check mode detects a stop that leaves the pair at different revisions.
The review catalog, manifest, and canonical spec paths MUST resolve to three
distinct files. Sync and check MUST refuse equal paths and symlink or hard-link
aliases before writing, so a destination typo cannot overwrite the human-owned
catalog.

The machine surface includes entrypoint and route invocation mode, including
whether an empty argument vector shows help before parsing or is passed to a
single-command parser. Required syntax may still reject it. It also includes
route paths and parser subcommand aliases,
delegated paths, positional and option IDs/shapes, option aliases, defaults,
choices, requiredness, scope/placement, exclusive groups, synopsis, behavior
and confirmation policy, parser-scoped `allow_abbrev`, argparse's
negative-number matcher and whether that parser registers negative-number-like
options, and actions added by parser callbacks. The JSON surface schema version is `5`; each route records
`single_command` and `no_args_action`, and those values participate in
candidate signatures so a change to empty-invocation behavior requires review.
Each route also records
`parser_settings` along its path. Each setting contains `parser_path`,
`allow_abbrev`, `prefix_chars`, `fromfile_prefix_chars`, the
`negative_number_matcher` pattern and flags, and
`has_negative_number_optionals`. These parser token rules participate in
candidate signatures; a custom or uninspectable negative-number matcher makes
the surface incomplete. Registered `surface_id` values
preserve identity through a rename; otherwise default IDs are derived from the
route and the declared verb/argument/option spelling. Route IDs are unique;
action IDs are unique within their route. Single-command entrypoints MUST NOT
claim the `help <verb>` builtin. Help wording is not identity.

The generated Markdown route table MUST show the route's invocation mode and
empty-argv result, help summary and description, mutation/behavior labels,
confirmation availability, synopsis and usage overrides, delegated
metadata, parser settings including negative-number token handling, whether a
parser callback ran, opaque fields, and
whether syntax is complete. Its argument and option table MUST show each
description, token shape, argparse action and converter, const, requiredness,
choices, declared and effective defaults, exclusive-group requiredness,
scope and parser placement, and hidden/help-group status. `hidden` options
remain present in the semantic surface, labelled hidden; hiding an option
removes it from operator help, not from the audit. Callable converters and
custom actions MUST be visible by stable import label in the generated table
and manifest.
The exporter may enumerate choices only when the runtime container is an exact
built-in list, tuple, set, or frozenset and every member is an exact built-in
string, integer, finite float, or boolean. A custom container or container
subclass MUST be marked opaque because its membership behavior can differ
from its items. An `Enum` member, custom scalar subclass, or other non-scalar
choice MUST also be marked opaque and make that parser surface incomplete;
serializing its display text or `.value` could change the runtime equality
rule used by argparse.
An option with `nargs=0` and non-`None` `choices` MUST also make the surface
incomplete because argparse does not check choices for flag-only actions.

Nested parser routes carry forward every action from their parent parser that
is accepted before the nested command word. Each action records its parser
path and whether it must appear before a nested subcommand; options belonging
to the nested parser are recorded at that deeper path. A parser that requires
a nested command is a route prefix, not a runnable invocation candidate. Its
executable child routes contain the inherited parent arguments and options.
Delegated command groups are also route prefixes: the generated checklist
belongs to their child routes, not to the grouping word by itself. Their child
paths MUST appear in the human-readable route table.
Multiple nested subparser groups at one parser depth are marked incomplete
until the exporter can represent their invocation order without ambiguity.
Delegated single-command routes retain metadata for every wrapper in a nested
delegation chain and for the final delegated command. The Markdown shows these
contracts separately, and their behavior/confirmation fields affect candidate
signatures. Wrapper-local arguments/options/callback syntax that
the delegate runtime does not apply, and inherited global options absent from
the delegated parser, MUST mark the surface incomplete. A delegated parser's
inherited global actions MUST preserve the parent action's flags, destination,
action type, `nargs`, converter, choices, constant, default, requiredness,
metavar, and mutually-exclusive group membership and requiredness; a mismatch
makes every route under that delegate incomplete because
the wrapper and child can split or interpret the same leading tokens
differently. Custom converters and action classes must resolve to the same
runtime objects in both parsers; matching import labels alone are not proof of
identical behavior.

The generated checklist is a bounded set of review dimensions, not a set of
invented executable examples or inferred outcomes. It covers minimum valid
invocation syntax,
each positional shape and enumerable choice, each product-owned option
spelling and choice, exclusive alternatives/conflicting pairs, parser route
aliases, and catalog-declared option interactions, including a target route
combined with an option registered only on another route. Such a case's
signature MUST include the foreign option's owning route and action shape.
Every generated candidate MUST also include the target route's required
positional, required option, and required-exclusive-group contracts in its
signature. Check mode MUST require that baseline for every candidate kind so an
option or alias case cannot be certified with syntax that fails before that
dimension is reached. An `exclusive-conflict` candidate may violate the group
under test; an interaction may do so only when it explicitly names multiple
members of that same group. All other required baseline actions remain
mandatory.
Check mode MUST require each named local option, the foreign spelling as an
unrecognized option token at the target route's parser depth, and that route's
required positionals, required options, and required-exclusive selections so
the case isolates the intended interaction. A value-taking foreign option
MUST be tokenized using the owner's action arity while using option-like target
tokens as value boundaries; its value tokens MUST NOT be assigned to a target
positional. Check mode MUST enforce the declared arity and enumerable choices
for every occurrence of each participating option; a valid first occurrence
MUST NOT hide a malformed repeat. For choices, check mode MUST apply the exact
`builtins.str`, `builtins.int`, `builtins.float`, or `builtins.bool` conversion
recorded in the surface before checking membership and whether an invocation
supplies its reviewed choice. With no converter, it MUST compare the raw argv
string to the serialized choice value. For an option with `nargs="?"`, an
occurrence with no value MUST check the declared `const` against choices using
the same converter rule. Static choice checks apply only to
`argparse._StoreAction`, `argparse._AppendAction`, and
`argparse._ExtendAction`; custom actions remain opaque and require a linked
behavior test. It MUST NOT execute consumer-defined converters, custom
actions, or handlers; those remain behavior-test oracles.
A flag-only foreign option MUST reject an inline value. If an option ID is
ambiguous across routes and does not resolve uniquely on the target route,
surface generation MUST refuse it.
The catalog owns the expected decision and status, including for cross-route
interactions; check mode MUST NOT infer product semantics from route ownership.
For the `show --poll` example above, the consumer records a refusal because
that product's `show` command rejects the watch-only option. Any unknown
option not named by the interaction MUST be reported as a finding, so an
unrelated typo cannot be treated as part of the reviewed case.
The generator does not automatically enumerate every value count or repeated
occurrence for optional-arity, variadic, or repeatable options; consumers MUST
declare distinct named interactions when those invocation shapes carry
different product meaning. It MUST NOT enumerate the
full power set of switches. The default cap is 512 candidates; a product may
raise the cap explicitly. The exporter MUST fail on overflow instead of
truncating. The standard library owns its common controls such as verbosity,
color, and progress behavior; their syntax remains in the manifest, while
product-sensitive common options (`--json`, `--yes`, and `--debug-raw`) remain
review candidates. A product-specific common-option interaction belongs in
its TOML catalog.

Callable converters and custom argparse actions are reported by stable import
label and marked opaque. An opaque validator does not make otherwise visible
syntax incomplete; product cases and tests still own its accepted values and
failure boundary. An unenumerable parser field or missing parser route MUST
make the surface incomplete and fail check. The exporter MUST never discard a
field or serialize an unstable object representation to imply completeness.
The exporter MUST identify built-in `str`, `int`, `float`, and `bool`
converters by exact runtime object identity. A custom callable with a colliding
import label MUST remain opaque. Static choice checks MUST also identify stock
`_StoreAction`, `_AppendAction`, and `_ExtendAction` classes by exact runtime
class identity; a custom action with a colliding label remains opaque.
Any non-`None`, non-callable type reference on a value-taking action MUST make
the surface incomplete. Custom parser type-registry registrations MUST also
make the surface incomplete because a registry key can change the converter
resolved for an action.
For `nargs="?"`, a non-`None` `const` that is not an exact built-in string,
integer, finite float, or boolean MUST make the surface incomplete because its
choice equality cannot be represented safely.
Ordinary parser callbacks that add inspectable argparse actions are supported.
A callback or parser subclass that replaces an argparse token-parsing method,
sets uncaptured parser-level defaults, or leaves `_option_string_actions`
inconsistent with the parser's actions MUST make the surface incomplete; an
action inventory alone cannot describe that parser's accepted syntax.

The TOML catalog has `schema_version = 1`, `cli_id`, optional
`max_candidates`, `interaction_groups`, and `[[cases]]` records. Each active
case MUST contain a generated stable ID, `decision = "accept"` or `"refuse"`,
the current `reviewed_signature`, a rationale, explicit invocation argv,
expected process status, explicit effects (including `[]` when there are none),
and one or more exact pytest node IDs. Invocation argv is passed to
`RegisteredCli.run()` and excludes the executable name. It MUST contain real
product-owned values; the generator does not guess selectors, UUIDs, paths, or
provider names. An empty argv is valid only for a genuine no-token
single-command case. A retired case MUST stay in the catalog with a human
retirement reason; if that ID appears again, it requires explicit
reactivation and review.

Check mode structurally checks invocation argv against the declared route. It
MUST account for recognized option arity, option scope, parser depth, and
parser-depth `--` terminators when locating command words. It MUST account for
required parent positionals before nested commands, including a remainder
positional yielding to a registered nested command. For `argument-shape` and
`argument-choice` candidates, it MUST assign tokens to the named positional
action at its parser depth; finding the same text in a sibling positional does
not satisfy the candidate. A flag-only option with an inline value is not a
valid occurrence. Every positional token MUST map to a declared positional at
that parser depth; unassigned tokens MUST be reported as findings. It MUST
resolve long-option abbreviations using the
`allow_abbrev` setting of the parser at that depth. A non-default `prefix_chars`
or enabled `fromfile_prefix_chars` MUST make the surface incomplete until the
checker can represent those token rules. A callback that replaces an argparse
token-parsing method or `_registry_get`, adds any non-default type-registry
registration, sets uncaptured parser-level defaults, or leaves the
parser's option-action lookup inconsistent MUST also make the surface
incomplete. Ordinary callbacks that add inspectable argparse actions remain
supported. In every generated candidate, a
required option counts only
when it is an active option token, its declared minimum values are supplied,
and exactly one alternative is present for each required exclusive group.
Repeated occurrences of that same member remain valid when argparse accepts
them; consumers declare an interaction when repetition has separate product
semantics. It
MUST NOT call `RegisteredCli.run()`,
`ArgumentParser.parse_args()`, custom converters, custom argparse actions, or
command handlers. This check is not a full parser acceptance oracle: the
referenced product test MUST run the real invocation and assert its outcome
and effects. A marker proves only that the named test is collected and linked
to the semantic case.

If an interaction group references a route or option that no longer exists,
direct surface export MUST refuse the invalid reference. Sync MUST still
generate the current grammar, preserve the catalog bytes, list the interaction
reference as needing repair, and retain its semantic case in the stale-case
section. Check MUST report the same reference and fail until the owner repairs
or retires that case; sync MUST NOT edit the TOML catalog.
For a single-command entrypoint, the empty route path denotes its one parser;
the check MUST still recognize that parser's declared options.

Candidate signatures cover the relevant route and parser shape, including
aliases, defaults, choices, requiredness, action/nargs, scope/placement,
exclusive relationships, behavior and confirmation policy, delegated command
identity/group/behavior/confirmation metadata, and synopsis overrides.
Descriptions and help wording are excluded so copy edits do not force semantic reapproval.
Changed relevant shape requires a new decision. Removed decisions remain in
the generated specification until a person retires them; the generator never
deletes rationale, effects, or test references.

The provided `python -m cli_extended.surface_cli` command accepts an
import-safe `--factory module:callable` that returns the consumer's
`RegisteredCli`. `sync`, `template`, and `check` are the supported operations;
consumers do not write a parser walker, table renderer, or region merge.
`assert_cli_case_tests(collected_items, catalog)` verifies that each active
case's exact node IDs are collected and carry matching
`pytest.mark.cli_case(case_id)` markers. It rejects unknown/non-active markers
and statically skipped referenced items where their skip condition is
literally true. This proves collection/linkage only: a test can still be weak
or fail. The consumer's normal gate MUST execute the referenced behavior tests
and assert the expected output, status, and side-effect boundary.

When adding, removing, renaming, or changing a public command or option, the
consumer MUST review both the generated grammar diff and the semantic table.
That product review should ask whether the verb and grouping still match real
operator workflows, whether any alias is legacy-only, what each omitted or
defaulted value does, which options conflict or depend on one another, and
which files, state, network, credentials, confirmation, and output each case
can affect. The generic library reports surface facts; it does not decide
whether a product supports the right use cases.
