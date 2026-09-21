# User-facing Python CLI standard

This document defines the common command-line contract for user-facing Python
CLIs in this repository. It applies to installed commands, repository scripts,
and executable Python entrypoints. A tool may add domain-specific behavior, but
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

Unknown verbs, missing required values, and malformed options print a concise,
identity-headed diagnostic to stderr and exit `2`. They may include the
relevant usage synopsis, but must not print a raw traceback.

Once a known verb has been identified, a command-local invocation error also
prints that verb's complete help immediately. This includes missing required
positional arguments, missing required options, invalid choices, invalid
values, and invalid option combinations. The operator should not have to type
the same command again with `--help` to discover the remedy. The output must
contain the failed-operation diagnostic and the same command help available
from `tool <verb> --help`, and must still exit `2`.

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

An entry has the form:

```text
  verb [required arguments] [important options]
      one-line explanation of the observable operation
```

Top-level entries should say whether they are read-only, mutating, interactive,
or potentially expensive. A mixed verb appears once under `MIXED OPERATIONS`;
its help entry lists its read-only default and its mutating actions together.
For example:

```text
  firewall SERVER [MAC] get|set
      inspect or replace the interface assignment; set is confirmed
```

Nested actions are actions, not top-level verbs and not boolean options. They
are written as positional action names (`snapshots SERVER create`), not as
misleading flags (`snapshots SERVER --create`).

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

The default is `info`. `--quiet` is an alias for `--log-level=error`, and
`--debug` is an alias for `--log-level=debug`. `--debug-raw` implies debug
verbosity in addition to disabling supported redaction. The level controls
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

The normal interactive prompt must clearly state what will change. A declined
prompt and an intentional Ctrl-C are clean cancellations, not tracebacks.
When stdin is not interactive, a command must not attempt a prompt that will
fail with EOF. It must either have a documented non-interactive default or
refuse with an actionable message naming `--yes` or the required input. EOF is
handled as a clean refusal/cancellation, never as an uncaught traceback.

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
within that CLI and documented in its help.

## 6. Errors, exceptions, and cancellation

The CLI distinguishes expected operator errors from programming failures.

- Expected validation, configuration, authentication, API, filesystem, and
  subprocess failures are caught at the CLI boundary and rendered as concise,
  meaningful messages that state the failed operation and, where possible, a
  corrective action.
- Expected failures do not print Python tracebacks.
- `KeyboardInterrupt` is always handled as intentional cancellation. It prints
  a short cancellation message, never a traceback, and exits `130`.
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
stdout is not a TTY, when `NO_COLOR` is set, or when the CLI provides
`--no-color`. Semantic meaning must remain available in plain text. If both
explicit colour controls are exposed, `--no-color` wins over automatic colour
and `--color` may explicitly override `NO_COLOR`; JSON output never uses ANSI
colour. Supplying both explicit controls is an invocation error rather than a
last-option-wins rule.

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
final event.

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
9. unexpected exceptions remain distinguishable from handled failures, with
   `--debug` providing additional diagnostics and `--debug-raw` explicitly
   proving the redaction boundary is disabled only when requested;
10. severity tags, hints, colour/`NO_COLOR`, and each supported log level
    remain meaningful when output is redirected;
11. JSON mode keeps stdout machine-readable and diagnostics on stderr; and
12. help/version paths work without credentials, configuration, network, or
    runtime services.

Tests should exercise the real entrypoint or module invocation, not only helper
functions. A controlled bad input must demonstrate that each safety/error
oracle actually goes red before the fix.

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
  metadata table used to render top-level help and validate its tests.

The shared helper is free to reuse established Python libraries where they
materially improve correctness, presentation, terminal handling, structured
output, or testing. Such dependencies must be deliberate and documented; the
helper must wrap them behind this repository's observable contract so a library
upgrade cannot silently change help layout, error semantics, redaction, or exit
statuses. A CLI should not acquire multiple overlapping parser/rendering
frameworks merely because each project chose a different one.

The repository has enough repeated behavior across CIU, CMRU, nyxloom, and the
Netcup tools that a small custom library is worthwhile once migration begins.
It should be a focused contract layer, not a replacement for every possible
CLI framework. It may use or adapt an established parser/renderer, but its
narrow API should cover:

The recommended home is `libraries/cli-extended/`, with distribution name
`cli-extended` and Python import name `cli_extended`. It must be independently
packageable so installed CLIs do not depend on importing from the repository
root.

1. `CliIdentity` and authoritative version resolution;
2. structured verb/group metadata and top-level help rendering;
3. a common parser wrapper for `--help`, `--version`, `--debug`, `--yes`,
   `--debug-raw`, `--log-level`, colour/progress controls, and
   command-help-on-error;
4. severity-tagged diagnostics, hints, TTY-aware colour, and progress
   rendering with a plain fallback;
5. the outer exception and Ctrl-C boundary; and
6. reusable contract-test helpers for side-effect-free help/version, output
   streams, exit statuses, and prompt behavior.

Domain verbs, API clients, configuration loading, and destructive-operation
policy must remain in each owning project. The helper must not import CIU,
CMRU, nyxloom, Netcup, Docker, or any project-specific configuration. It must
also be packaged or otherwise made available to installed tools; relying on a
repository-root import path would make a standalone CLI work only from this
checkout.

Existing project-local helpers (`ciu.cli_utils`, `cmru.cli_support`, and
nyxloom's parser classes) are useful migration evidence, but keeping three
independent implementations will eventually recreate the drift this standard
is intended to prevent. A later implementation change should first build the
helper and migrate the three Netcup entrypoints, then migrate the larger CLIs
with compatibility tests.

## 11. Further contract areas

The following areas should be covered by the standard or explicitly marked
tool-specific before a CLI is considered fully adopted:

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

This is an adoption plan, not a claim that all current tools already conform.

| CLI | Main adoption work |
|---|---|
| `ciu` | align `help` verb and remove `-h`; retain its strong grouped/help model |
| `cmru` | scope options to the selected verb; align `help`, `--yes`, and exception behavior |
| `nyxloom` | make bare invocation exit `0`; remove flat parser list; align version output and `help` |
| `scp-api.py` | add version/help verbs, remove duplicate verb list, add identity header, align `--yes`/errors |
| `install-host.py` | add identity/version/help verb, grouped options, and common error/debug behavior |
| `monitor-task.py` | make bare/help invocations side-effect free; prevent `help` from being parsed as a UUID |
| other `scripts/` CLIs | audit and adopt the same contract when they are user-facing |

The first migration target is `scp-api.py`, `install-host.py`, and
`monitor-task.py`, because the audit found both discoverability defects and a
real unsafe `monitor-task.py help` path. The standard itself is repository-wide
and does not require every CLI to be migrated in one change.
