# cli-extended consumer guide

`cli-extended` standardizes CLI mechanics; it is not an application framework
and does not own product behavior. The normative behavioral contract is in
[`SPEC.md`](../SPEC.md). The adopting CLI declares its public interface once
with `CliRegistry`, then supplies domain-specific parsers and handlers only
where needed. The package's
[`README`](../README.md) contains the complete registration pattern.

## Install and choose a version source

For development in this checkout:

```bash
python3 -m pip install --editable ./libraries/cli-extended
```

For an independently deployed CLI, install the built `cli-extended` wheel in
the same environment as the CLI. Do not add repository-root `PYTHONPATH`
assumptions to an installed executable.

The CLI owner must supply a version from an authoritative source. Prefer
`CliIdentity.from_distribution()` when the application's distribution is
installed. For source-run tools, pass the version from that project's checked-in
version module/metadata. Do not invent a fallback. `cli-extended` itself uses
its explicit `[project].version` in its `pyproject.toml`.

## Replacing hand-rolled version lookup, exception wrappers, and --dry-run

Three patterns recur in adopted CLIs and are now library features.

**Version lookup.** Netcup's `monitor-task.py` read a `VERSION` file with its
own regex. Before:

```python
_VERSION_PATH = Path(__file__).resolve().parent / "VERSION"

def _read_version() -> str:
    version = _VERSION_PATH.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError(f"invalid Netcup CLI version in {_VERSION_PATH}: {version!r}")
    return version

IDENTITY = CliIdentity(name="NETCUP SCP", command="monitor-task",
                       version=_read_version(), long_name="Netcup task monitor")
```

After (add `distribution=` as well once the script ships as a wheel):

```python
IDENTITY = CliIdentity.resolve(
    name="NETCUP SCP",
    command="monitor-task",
    long_name="Netcup task monitor",
    version_file=Path(__file__).resolve().parent / "VERSION",
)
```

**Exception wrapper.** nyxloom's `_invoke` wrapped every handler to print
`error: ...` and return `1` unless `--traceback` was given, and declared its
own `--traceback` option. Before:

```python
try:
    return handler(args)
except (CliFailure, PromptCancelled, KeyboardInterrupt, SystemExit):
    raise
except Exception as exc:
    if bool(getattr(args, "traceback", False)):
        raise
    print(f"error: {exc}", file=runtime.output.stderr)
    return 1
```

After: delete the wrapper and the consumer `--traceback` option (the library
refuses a duplicate), and register the policy once:

```python
registry = CliRegistry(
    identity, prog="nyxloom", description="...",
    unexpected_exceptions="report",
    expected_exceptions=(DomainError,),
)
```

**`--dry-run`.** Netcup's `install-host.py` declares its own `--dry-run` and
reads it with `getattr(args, "dry_run", False)` in many places. Before:

```python
if getattr(args, "dry_run", False):
    print("DRY RUN: would reinstall", server)
    return 0
```

After: mark the verb and let the confirmation gate do the work. Remove the
consumer `OptionSpec(("--dry-run",), ...)` first; `build()` refuses both.

```python
VerbSpec("install", description="Install a host.", mutating=True,
         dry_run=True, handler=install)

def install(args, runtime):
    runtime.output.info(f"would reinstall {args.server}")  # preview first
    if not runtime.confirm(f"Reinstall {args.server}?"):
        return 0  # --dry-run prints "Dry run: no changes made." and lands here
    reinstall(args.server)
```

Until a CLI migrates, its own `--dry-run` stays legal as long as no verb in
that CLI sets `dry_run=True`.

## Replacing handler-side option checks and name-list parsing

**Declared constraints.** CMRU's `tool-deps` handler refused `--dry-run`
without a mode and `--refresh` with `--json` after parsing. Before:

```python
def tool_deps(args, runtime):
    if args.dry_run and not (args.update or args.write or args.refresh):
        raise CliFailure(
            "--dry-run requires --update or --write or --refresh",
            exit_code=2, show_help=True,
        )
    if args.refresh and args.json:
        raise CliFailure("--refresh cannot be combined with --json",
                         exit_code=2, show_help=True)
    ...
```

After: delete both checks and declare them once. The flags must be accepted
by the verb (local, global, or a library control such as `--json` or
`--dry-run`), and every referenced option must default to `None`, `False` or
an empty list, or `build()` raises `ValueError`.

```python
from cli_extended import Conflicts, Requires, VerbSpec

VerbSpec(
    "tool-deps", description="Report or update tool dependencies.",
    mutating=True, dry_run=True, handler=tool_deps,
    options=(
        OptionSpec(("--update",), "Update pins.", parser_kwargs={"action": "store_true"}),
        OptionSpec(("--write",), "Write the lock.", parser_kwargs={"action": "store_true"}),
        OptionSpec(("--refresh",), "Refresh metadata.", parser_kwargs={"action": "store_true"}),
    ),
    constraints=(
        Requires("--dry-run", ("--update", "--write", "--refresh"),
                 "a dry run needs a mode to preview"),
        Conflicts(("--refresh", "--json"), "refresh output is not JSON"),
    ),
)
```

`tool-deps --dry-run` now exits `2` with
`--dry-run requires --update or --write or --refresh: a dry run needs a mode
to preview`, followed by the verb's help, whose `CONSTRAINTS` section lists
both rules. `RequiresChoice("--timeout-scope", "--mode", ("fast",), reason)`
covers "this option applies only in that mode". Leave rules that read loaded
configuration or runtime state in the handler. Run `cli-extended surface sync`
afterwards: each constraint is a new review candidate
(`constraint-requires`, `constraint-conflict`, `constraint-choice`), and the
catalog records the product decision and a test for each refusal. A case must
include the rule's trigger (the `option` of a requires/choice rule, any member
of a conflict) or check reports it as not exercising its constraint.

Referenced options must have a readable presence: `build()` also refuses an
option that shares its `dest` with another option (`--color`/`--no-color`, a
`store_true`/`store_false` pair), `nargs="*"`, `nargs="?"` whose `const`
equals its default, and a `RequiresChoice` target that is list-valued
(`append`, `extend`, or `nargs` other than none or `"?"`).

**One-time re-sync.** Every route in the manifest now carries a
`"constraints"` key (empty when none), so every committed manifest and
generated spec region is stale until it is re-synced. Do that once, together
with the schema-7 re-sync, with `cli-extended surface sync`; unconstrained
routes keep their existing signatures.

**Selector lists.** CMRU parsed `all`, one name, or `a,b` with
`parse_target_names` and resolved it later in `select_target_names`. Register
the type instead and keep the resolution against loaded data in the handler:

```python
from cli_extended import ArgumentSpec, SelectorList

ArgumentSpec(
    "targets", "Project names, comma-separated, or 'all'.",
    parser_kwargs={"type": SelectorList(), "nargs": "?"},
)

def handler(args, runtime):
    if args.targets is None:
        names = default_targets()
    elif args.targets is SelectorList.ALL:
        names = every_project()
    else:
        names = list(args.targets)   # given order, validated structure
```

Differences from `parse_target_names` to account for when adopting:

1. It returns a tuple, not a list; the absent argument stays `None` (argparse's
   default) instead of being passed through the parser.
2. `all` yields `SelectorList.ALL` (no `choices`) or the full `choices` tuple,
   not `["all"]`.
3. Failures raise `argparse.ArgumentTypeError`, which becomes a usage error
   with exit `2` and the verb's help, not `TargetSelectionError`; the message
   wording differs (`empty selector item in 'a,'`, `duplicate selector 'a'`,
   `'all' cannot be combined with other names`).
4. The check order is empty item, then `all` mixed, then duplicate. For
   `all,all` CMRU reports a duplicate; `SelectorList` reports the mix.
5. Unknown names are rejected at parse time only when `choices` is given. CMRU
   validates names against the loaded registry in `select_target_names`, so
   keep that check (or pass the loaded names as `choices`).
6. `select_target_names` also reorders to declared project order and applies
   the context-project and estate-scope defaults; `SelectorList` returns names
   in the order given and applies no defaults.
7. Surrounding whitespace is stripped from each item in both; `SelectorList`
   additionally refuses `choices` or an `all_token` that carry surrounding
   whitespace, since such a name could never match.

## Turn an interface inventory into registrations

Before editing parser code, write down the operator-facing verbs/actions and
classify each as exploration, modification, mixed, setup, or maintenance.
Mark whether it mutates state, prompts, or can take a long time. Then express
each interface element once:

- `VerbSpec`: name, optional synopsis override, full user-facing command
  description, semantic group,
  optional shorter `summary_description` for the top-level catalog, examples,
  behavior attributes, handler, positional arguments, and options;
- `ArgumentSpec`: positional name, metavar, description, and argparse
  attributes such as `nargs`, `choices`, or `type`;
- `OptionSpec`: flags, display metavar, help group, description, argparse
  attributes such as `action`, `choices`, or `default`, structured
  required/exclusive-group metadata, and `hidden=True` for supported internal
  options that must not appear in public help.

`CliRegistry` turns those declarations into argparse parsers, grouped terminal
help, full Markdown reference help, a dispatch map, and common shell options.
The registry's `description` is the overall CLI purpose shown after usage in
the top-level catalog; it is distinct from each verb's `description`.
It also checks duplicate command names and catalog/parser consistency. The
consumer should not separately hand-maintain a command list, parser map, and
help list.
Registered handlers may return `None` for success; `RegisteredCli.run()` maps
that result to process status `0`. Prefer `None` for ordinary successful
handlers and return an integer when the product defines a meaningful status.
This keeps successful handlers concise and leaves exit-status conversion at
the shared CLI boundary.
Long options must be supplied exactly as declared: the registry disables
argparse's prefix abbreviations at the root and verb parsers. Set
`allow_abbrev=True` on `CliRegistry` only if partial spellings are an intentional
public contract; doing so can make a future option rename ambiguous or revive a
retired prefix accidentally.

The library derives usage syntax from declared positionals and option
requirements. Leave `VerbSpec.synopsis` unset for this derived grammar; use an
override only for a public syntax shape the metadata cannot express. For
example, declare two `OptionSpec`s with a shared
`mutually_exclusive_group="configuration-source"` and
`mutually_exclusive_required=True` to enforce and render exactly one required
config source. Do not separately write a usage string that can drift from the
parser. Use `VerbSpec.configure(parser)` only for genuinely custom structures
such as nested positional actions. Keep common arguments/options in the
structured fields so they appear in generated Markdown too.

Place options according to where they apply: invocation-wide options belong in
`CliRegistry.global_options` and work before or after command selection;
command-local options belong in that verb's `options` and take precedence for
the same complete flag set. Give every option an intentional
display group (`OUTPUT`, `FILTERS`, `STOP CONDITIONS`, etc.) instead of grouping
by implementation detail. Use `VerbSpec.group` for the semantic top-level
verb group. Use `summary_description` only to shorten the one-line top-level
catalog entry; keep the full behavior and caveats in `description` and
examples. The same metadata drives terminal and Markdown help; avoid a
parallel manually formatted epilog for ordinary options. Custom parser
callbacks are an escape hatch for syntax argparse cannot express through the
structured fields, not the default registration path.

`VerbSpec.description` is the full description of one verb, not the overall
CLI description. It is passed to argparse as the command parser description and
appears after that command's generated usage line, with blank lines separating
the sections. Use `summary_description` only for a shorter top-level catalog
entry. The overall CLI description is supplied once to `CliRegistry`.
The top-level grouped list shows verb names without syntax fragments, and
calculates one aligned description column across all groups. Full argument and
option syntax is kept in `tool VERB --help`.

Supported common output/debugging controls are accepted before or after the
selected verb with the same meaning. A CLI may support `--json` or
`--progress` only for some verbs: top-level help can list the union, but
command help is authoritative, and using an unsupported selector must fail
with that verb's help instead of being ignored. For options that do apply,
`tool --quiet status` and `tool status --quiet` must behave alike; combining
`--quiet` with `--debug` must be rejected in either order. Do not duplicate a
command-local option at the root merely to make it appear in more than one
place; register genuinely invocation-wide options once as global options.

For a tool with one operation and no verb token, use
`CliRegistry(single_command=True)`. This is a supported shape, not a reason to
keep a UUID or positional word ambiguously doubling as an action. If operators
have meaningfully different operations, give those operations explicit verbs.
For example, a task monitor can distinguish `show TASK_UUID` (fetch once) from
`watch TASK_UUID` (poll with progress until terminal). A former `--dry-run`
that only fetches the task once is not a third operation; fold that behavior
into `show` or define a genuinely different preflight contract before adding
`check`. Likewise, add `wait` only if it has a useful automation contract
distinct from `watch`. Keep cancellation in the API-owning CLI if it already
owns task mutations; do not create two interfaces for the same operation by
default.

### Review caller surfaces and command semantics

Treat adoption as a product interface review, not a mechanical translation
of the old parser. `CliRegistry` keeps declared syntax, help, and dispatch in
sync; it cannot decide which workflows deserve verbs, what a missing option
means, or which effects a command performs.

Before implementation, inventory every shipped caller surface: installed
console scripts, supported `python -m` entrypoints, repository or project-step
adapters, bootstrap commands, public Python APIs, and generated standalone
tools. Classify each surface by its intended caller, whether it is installed in
the wheel, and the contract it owns. A module may be a library without being a
CLI; an active module adapter may be required by project contracts even when
operators rarely invoke it. Remove a spelling only after confirming that it
adds no distinct caller workflow, then document the supported replacement.

For each supported CLI and leaf verb, keep a product-owned semantic table in
the canonical product specification. Record the use case; positional
selectors; accepted values and defaults; what omission means; selection
scope; valid and refused option combinations; filesystem, state, network, and
credential effects; dry-run and confirmation boundaries; output and exit
status; and the decision behind the public spelling. This semantic record
complements the registry's generated grammar. It does not belong in a generic
library because its truth comes from the owning product.

Keep the generated surface and semantic review in the same canonical CLI
specification, but give them separate ownership. The generator owns only a
clearly marked grammar inventory; the product owns use cases, combination
decisions, effects, rationale, and test evidence keyed to stable surface and
option IDs. A generator must update only its marked region, never rewrite the
whole specification or its semantic rows. On re-run, added or changed grammar
must appear as a reviewable diff and mark affected semantic entries as needing
review. Removed grammar must remain visible as stale until a reviewer records
its retirement; generation must not silently delete the associated decision,
rationale, or evidence. A check mode should fail on grammar drift, missing
semantic coverage, unresolved stale entries, or pending review. This makes
regeneration repeatable while keeping product decisions durable.

#### Current generator coverage

The library exposes a stable machine-readable CLI manifest and a merge-aware
specification generator alongside Markdown help. Generated help is not a
substitute for the semantic audit. The live catalog pilot is Netcup's
[`monitor-task.py` CLI spec](../../../scripts/netcup/CLI-SPEC.md), with its
[`cli-review.toml`](../../../scripts/netcup/cli-review.toml), generated JSON
manifest, and pytest collection hook. It demonstrates regeneration and test
linkage on a real hyphenated script. CMRU's
[`S-CLI.9`](../../../cmru/docs/SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit)
grammar and semantic tables remain hand-authored, with gate checks that
compare the documented grammar to the live registered parsers. Its owner can
adopt the shared catalog/generator separately after assessing the generated
surface against the full CMRU interface.

The first-party pilot declares its CLI in a standalone `cli-extended.toml`
(it has no pyproject) that uses the script-path factory form, and runs the
shared command from that directory:

```bash
cd scripts/netcup
PYTHONPATH=../../libraries/cli-extended/src${PYTHONPATH:+:$PYTHONPATH} \
python -m cli_extended.cli surface check
```

When a registry changes, run `surface template` to discover new or stale review
rows, review their invocations and effects, and edit the TOML decisions. Run
`surface sync` to refresh only the manifest and marked Markdown region, then
link collected behavior tests with `cli_case` markers. The consumer's gate
checks both catalog coverage at collection and behavior by executing the tests.
Finish with read-only `surface check` and review its diff. The marker proves
linkage only; each referenced test must assert the promised output, status, and
effects.

Surface export and test helpers start from the built registry and
publish a stable interface for verbs, delegated command paths, positional
shapes, declared flags and aliases, option action/value shape, requiredness,
choices, defaults, exclusive groups, and option scope. Custom parser callbacks
must be represented explicitly or marked for consumer-specific inspection;
the exporter must not quietly omit syntax it cannot understand. Use stable
IDs rather than rendered help text as join keys so wording and table layout can
change without losing semantic history.

The generator does not enumerate every subset of every option: the invocation
space grows exponentially. It generates the minimum route, positional shapes
and choices, option spellings and choices, exclusive alternatives/conflicts,
aliases, and each explicitly declared interaction. Product owners name the
meaningful conditional or forbidden combinations in the TOML catalog; the
library gives them stable IDs and signatures without guessing argv values or
outcomes. The product records whether each case is accepted or refused, why,
and which test proves parser, handler, or side-effect behavior. The shared
pytest helper checks that each referenced node is collected and marked; the
consumer's normal gate runs the test and checks its assertions.

#### Adopt the generator

Add two lines around one generated region in the product's canonical CLI
specification:

```markdown
<!-- cli-extended-surface:start -->
<!-- cli-extended-surface:end -->
```

Expose an import-safe factory that returns the normal `RegisteredCli`; it must
not call `app.run()` while being imported. A dotted `python.module:callable`
factory works for importable modules. For a single-file command that is not an
importable Python module (for example a hyphenated script), use
`path/to/command.py:callable`; the loader makes the script's sibling directory
available for imports, matching direct script execution.

Declare the CLI once in the project configuration. A project with a
`pyproject.toml` puts it under `[tool.cli-extended]`; a project without one
(like the Netcup scripts) uses a standalone `cli-extended.toml` with the same
keys at the top level. Relative paths resolve against the config file's
directory, `id` must equal the registered executable name, and `manifest` and
`spec` are given together.

```toml
# pyproject.toml
[tool.cli-extended]
schema_version = 1

[[tool.cli-extended.clis]]
id = "example"
factory = "example.cli:build_cli"
review = "docs/cli-review.toml"
manifest = "docs/cli-surface.json"
spec = "docs/SPEC.md"
findings = "docs/cli-review-findings.toml"
```

```toml
# cli-extended.toml (projects without a pyproject)
schema_version = 1

[[clis]]
id = "monitor-task"
factory = "monitor-task.py:build_cli"
review = "cli-review.toml"
manifest = "cli-surface.json"
spec = "CLI-SPEC.md"
```

The `findings` file is optional. Discovery walks up from the current directory
to the first directory holding either file (both in one directory is an error);
a malformed or unreadable `pyproject.toml` or `cli-extended.toml` met on the
way is an error naming the file, never skipped (pass `--config PATH` to bypass
it); `--config PATH` names one explicitly, and `--cli ID` picks a CLI when several
are configured. Then run the shared command from anywhere inside the project:

```bash
cli-extended surface sync
cli-extended surface template
cli-extended surface check
cli-extended surface report
```

`python -m cli_extended.surface_cli --factory ... --review ... --manifest ...
--spec ... {sync,check,template}` still works with its old flags, but prints a
deprecation warning; move to the config file.

#### The review loop

An agent (or a person) reviews the surface by a fixed loop, packaged as the
`cli-extended-review` skill (install it with `cli-extended skills install`):

1. `cli-extended surface sync` refreshes the generated manifest and spec region.
2. `cli-extended surface pack --output /tmp/review-bundle.md` writes one
   Markdown bundle: the rubric, every route's plain help, and every pending,
   changed, reappeared and stale case with its shape and current catalog row.
3. The reviewer judges each case and help block against the rubric and edits the
   catalog rows and the findings file by hand. The library never rewrites them.
4. `cli-extended surface sync`, then `surface check` (an open `blocker` or
   `major` finding, or a finding naming a route that no longer exists, fails it),
   then `surface report` for what is still open.

A findings file records what is wrong, with a concrete remedy:

```toml
schema_version = 1
cli_id = "example"

[[findings]]
id = "F-001"
status = "open"            # open | fixed | wontfix
severity = "major"         # blocker | major | minor | note
category = "semantics"     # grammar | help | semantics | consistency | adoption
route = "route:entrypoint:example/purge"
summary = "purge deletes without confirmation or --dry-run"
remedy = "declare mutating=True with dry_run=True"
```

A `wontfix` finding needs a `rationale`; an `open` one needs a `remedy`.

The catalog has two closed vocabularies. `state` is `pending` while a generated
case awaits review, `active` once its decision and test evidence match the
current signature, or `retired` after an owner records why the case no longer
applies. An active case's `decision` is `accept` or `refuse`; this records the
product's semantic ruling and does not replace the behavior test. The following
loader-valid example shows the required fields. Replace its generated ID,
signature, invocation, effects, and test node ID with values from the consumer's
surface and tests before running `check`.

```toml
schema_version = 1
cli_id = "example-tool"

[[cases]]
id = "case:route:entrypoint:example-tool/publish/minimum"
state = "active"
decision = "accept"
reviewed_signature = "sha256:replace-after-export"
rationale = "Publishing the selected staging artifact is a supported workflow."
invocation = ["publish", "staging"]
expected_exit_status = 0
expected_stdout_contains = ""
expected_stderr_contains = ""
effects = ["Publishes the selected staging artifact"]
test_ids = ["tests/test_cli.py::test_publish_staging"]
```

`sync` updates only the JSON manifest and marked Markdown region. It never
rewrites the TOML decisions or bytes outside the markers. It still writes the
new grammar when it reports pending reviews, so the diff makes additions and
signature changes visible. `template` prints TOML rows for new cases and
instructions for changed or reappeared cases; the product owner reviews them
and updates the catalog by hand. `check` is read-only and fails on stale
generated files, missing/pending cases, changed signatures, unretired removed
cases, or syntax the exporter could not enumerate. The default checklist cap
is 512; set `max_candidates` in the catalog or use the explicit command-line
override to raise it. Overflow is an error, never silent truncation.

The manifest starts from the built parser tree, including options and nested
actions added in `configure(parser)` and delegated CLIs. For a nested parser,
it carries parent actions onto the child route and records which parser level
accepts each argument or option. A required-subcommand parent is listed as a
route prefix; semantic cases belong to runnable descendants. Multiple nested
subparser groups at one parser level make the inventory incomplete so `check`
cannot certify an ambiguous route. Required-subcommand parents and delegated
command groups are route prefixes; checklist cases belong to their runnable
child routes, which remain listed under the parent in the generated table. The
manifest reports spellings,
aliases, choices, defaults, requiredness, argument shape, exclusive groups,
scope, placement, synopsis, parser-scoped `allow_abbrev`, callback-added
actions, and whether empty argv shows help before parsing or is passed to the
single-command parser. Required syntax can still reject an empty argument
vector after that parser runs. For the route table, this applies to the
executable entrypoint and single-command routes; ordinary command routes parse
their remaining tokens, route prefixes select nested commands, and delegated
groups pass remaining tokens to the child CLI. The table avoids claiming that
every multi-command route shows help when called without a remainder, since its
parser may dispatch or reject according to required syntax.
Route records must include boolean `single_command` and `no_args_action`
values. If a generated manifest is missing either fact, Markdown rendering
refuses it; correct the exporter or manifest source rather than filling in a
default.
It also records argparse's negative-number matcher and whether each parser has
negative-number-like options, so the checker classifies signed numeric values
using the built parser's rules; custom or uninspectable matchers make the
surface incomplete. Invocation mode and parser token rules participate in
candidate signatures. Its generated
Markdown renders each route's behavior and confirmation
policy, synopsis/usage overrides, delegated metadata, parser-callback status,
and opaque fields. Argument and option rows show descriptions, grammar shape,
argparse action and converter, const values, requiredness, choices and defaults,
exclusive-group requirements, scope and parser placement, and whether an
option is hidden from operator help. A change to abbreviation policy changes
case signatures. For nested delegated CLIs, every wrapper and the final
single-command's behavior/confirmation metadata are shown and signed;
wrapper-local parser declarations and inherited global options missing from
the delegated parser make the surface incomplete. A delegated parser must
preserve each inherited global's flags, destination, action type, `nargs`,
converter, choices, constant, default, requiredness, and metavar. A mismatch
also includes mutually-exclusive group membership and group requiredness, and
requires custom converter/action objects to be the same runtime objects in both
parsers. Matching import labels alone do not prove equivalent behavior. A
mismatch marks its routes incomplete because the wrapper and child can split
or interpret leading tokens differently. The checker uses the policy
for the parser that owns each option, including when a callback
configures a nested parser. Custom `prefix_chars` and argparse argument-file
expansion (`fromfile_prefix_chars`) make the surface incomplete because the
structural checker does not model their token syntax. A custom value converter
is marked opaque while the parser syntax remains inventoried; an unenumerable
grammar field makes the surface incomplete. Parser callbacks may add ordinary
argparse actions, which the exporter inventories. A callback or parser subclass
that replaces token-parsing methods, sets uncaptured parser-level defaults, or
leaves `_option_string_actions` inconsistent with the parser's actions makes
the surface incomplete; check mode will not certify that grammar. Set
`surface_id` on a declaration
to preserve its semantic identity through a rename. IDs are unique within a
route; descriptions and help wording are not identifiers. For an interaction
that references an option on another route, that option ID must resolve
unambiguously across the CLI; use a globally unique `surface_id` if the
route-derived ID is not convenient.

The generated checklist is symbolic. It covers minimum executable syntax, positional
shapes and enumerated choices, option spellings and choices, exclusive
alternatives and conflicts, parser subcommand aliases, plus combinations in
catalog `interaction_groups`. It does not invent real argument values, decide
whether a combination is valid, or expand the power set of all switches.
Library-owned common controls such as `--quiet`, `--debug`, `--color`, and
`--progress` are not in the grammar table. Each route lists the enabled ones on
one `Common controls: ...` line (the contract version is in the region header), and the library's
contract version (see
[What a library upgrade does to your surface](#what-a-library-upgrade-does-to-your-surface))
covers their syntax. `--json`, `--yes`, and `--debug-raw` are also consumer
review cases; declare an interaction when any common option participates in a
product rule. An interaction may still name a library control by its usual
option ID, for example `option:route:entrypoint:example-tool/publish/--json`.

#### What a library upgrade does to your surface

Your signatures cover only what your product declared. The library's own
controls (`--help`, `--version`, `--log-level`, `--quiet`, `--debug`,
`--debug-raw`, `--color`, `--no-color`, `--json`, `--progress`, `--yes`) are
named in the manifest, per route, and versioned by one integer, the *contract
version*. Upgrading the library normally leaves your manifest, spec region,
and every `reviewed_signature` byte-identical. An option of your own that
happens to be spelled `--json` is your grammar and stays in the table.

When a library release changes a control's syntax or meaning, it bumps
`CONTRACT_VERSION` and lists the change under the contract notes in the
library's `CHANGES.md`. Your next `check` then reports exactly one finding and
fails, with no per-case noise:

```text
cli-extended contract changed v1 → v2; read cli-extended CHANGES.md contract notes, then run sync
```

Re-sync like this:

1. Read the contract notes for the versions you skipped. Decide whether any
   product decision depends on a changed control.
2. Run `sync` (the command shown above). It writes the new
   `library_contract` version, each route's `common_controls`, and the
   Markdown region.
3. Run `check`. Any signature that really changed (for the reviewed
   `--json`, `--yes`, `--debug-raw` candidates, only their identity and route
   baseline are signed) now appears as an ordinary `signature changed`
   finding; review it and update `reviewed_signature` as usual.
4. Commit the manifest and spec together.

A manifest written before contract versions existed (no `library_contract`
record) is treated as an ordinary stale manifest: `check` reports
`generated CLI manifest is stale`; run `sync` once.

## Review cross-route and arity interactions

An interaction can describe a command with an option that belongs to a
different route. This records parser-level scope rules such as
`monitor-task show TASK_UUID --poll`, where `--poll` belongs to `watch` and
`show` must refuse it. The interaction's `route_id` names the command being
invoked; `option_ids` name the participating options wherever they are
registered:

```toml
schema_version = 1
cli_id = "monitor-task"

[[interaction_groups]]
id = "show-rejects-watch-only-options"
route_id = "route:entrypoint:monitor-task/show"

[[interaction_groups.combinations]]
id = "watch-poll-is-not-show"
option_ids = ["option:route:entrypoint:monitor-task/watch/--poll"]
```

The generated case signature includes the foreign option's owner route and
action shape. Check mode requires each named local option, the foreign spelling
as an unrecognized option at the target route's parser depth, and the target
command's required arguments, options, and exclusive selections. For a
value-taking foreign option, it consumes values according to the owner's
declared arity while treating option-like target tokens as boundaries. Those
tokens do not satisfy target positionals in the structural check. It validates
fixed/minimum arity and enumerable choices. For registered `type=str`,
`type=int`, `type=float`, or `type=bool`, it applies that exact built-in
conversion; invalid conversions are findings even when no choices are declared.
When choices exist, it compares the converted values to the declared choices.
For example, `type=int, choices=(1, 2)` accepts `--count 1`, while
`choices=(1, 2)` without a converter does not: argparse receives the raw string
`"1"` in that case. A custom converter remains opaque; its acceptance and
failure behavior belongs in the linked test, which invokes the real CLI and
checks status and effects. Built-in converters are recognized by exact
runtime object identity; a custom callable with a matching label stays opaque.
Static choice checks likewise require the exact argparse action class;
custom actions with a matching label stay opaque and need linked behavior
tests. Choice containers must be exact built-in lists,
tuples, sets, or frozensets with plain string, integer, finite-float, or
boolean members. Custom containers and other choice objects make the surface
incomplete because their runtime membership or equality cannot safely be
represented by the exported JSON values.
For an option with `nargs="?"`, invoking the option without a value selects
`const`. Argparse applies the declared converter only to string constants.
Whether the runtime checks an omitted constant against `choices` depends on
the stock runtime's behavior for that exact built-in type. The exporter probes
`None`, `bool`, `float`, `int`, and `str` constants with disposable parsers and
records the matching result in the surface and generated spec. It signs this
field into candidates. The structural check follows it without executing
consumer code. An omitted
optional positional uses its default, not `const`, and does not use this option
rule.
Any non-`None` option constant must be an exact built-in string, integer,
finite float, or boolean; `None` is probed as its own built-in case. Custom
scalar objects make the surface incomplete.
Argparse does not check `choices` for a flag-only action such as
`action="store_true"`; remove that unused declaration or use an action whose
invocation carries a value. The surface reports it as incomplete.
For positional `nargs=argparse.PARSER`, choices apply to the first converted
value only. `nargs=argparse.REMAINDER` converts tokens without checking
choices, so a remainder action with declared choices is marked incomplete.
Custom action classes remain opaque.
Any non-default parser type-registry registration makes the surface
incomplete. Argparse resolves registered converters by dictionary equality,
so even a distinct registry key that compares equal to an action's type can
change its value conversion.
Value-taking actions must use a callable converter or `None`. A non-callable
type reference with no registered converter is not valid argparse syntax, so
the surface marks it incomplete. Custom type registries are also incomplete.
The structural check does not prove product outcomes. For this `show --poll`
example, record
`decision = "refuse"` and a non-zero `expected_exit_status` because `show`
rejects the watch-only option. Check mode preserves the catalog's decision; it
does not infer the outcome from route ownership. The only unrecognized options
allowed are the foreign options named by the interaction. Reused option IDs on
multiple foreign routes are ambiguous and cause generation to refuse the
catalog.

If another command intentionally forwards foreign-looking options, record an
accepted decision only when its linked behavior test proves where those tokens
go. For example, a command with an `argparse.REMAINDER` tail can document
`exec PROGRAM --poll 5` as an accepted pass-through contract.

The generator bounds option interactions by the named groups in the catalog;
it does not infer which combinations have product meaning. It also does not
automatically enumerate every token count for optional-arity, variadic, or
repeatable options. If `--color` without a value and `--color VALUE`, or one
`--tag` and repeated `--tag` occurrences, have different meaning, declare
separate named combinations and write their exact argv in the semantic rows.
Every generated candidate carries the route's required positional, required
option, and required-exclusive-group baseline in its signature. Check mode
requires that baseline for every candidate kind and validates the declared
arity, modeled built-in conversion, and choices on every option occurrence; a
valid first occurrence cannot hide a malformed repeat. The structural checker confirms the route, named
option presence, declared value counts, enumerable choices it can model, and
required baseline syntax; behavior tests assert custom converter behavior and
exact value and repetition rules. Any bare token left after assigning declared positional
arguments is reported, so an unrelated extra value cannot be mistaken for a
valid option case. Those
distinctions belong in the consumer's canonical CLI spec and
should be re-reviewed whenever their signatures change.

If a route or option named by `interaction_groups` is removed or renamed, run
`sync` to regenerate the live grammar and keep the old semantic row visible in
the stale-case section. Sync records the unresolved reference and leaves the
TOML catalog byte-for-byte unchanged. Update the interaction and its reviewed
case or explicitly retire the case; check mode remains red until the reference
is resolved.

An active `[[cases]]` record must contain a stable generated `id`,
`decision = "accept"` or `"refuse"`, the current `reviewed_signature`, a
rationale, `invocation`, `expected_exit_status`, `effects`, and one or more
exact pytest `test_ids`. `invocation` is the argv passed to `app.run()` and
excludes the executable name; include the real verb path and product-owned
values. An empty invocation is valid only for a real no-token single-command
case. Empty `effects = []` explicitly means the case has no effects. Keep a
removed case in the catalog and set `state = "retired"` plus a reason only
after a product review. Do not accept the expected decision from generated
names, help text, or an AI-drafted table without verifying it against the
handler and tests.

The checker sanity-checks that argv reaches the declared route, accounting for
recognized option values, parser scope, parser-depth `--` terminators, and
required parent positionals before nested commands. Positional values are
assigned to the registered argument ID at that parser depth, so a choice value
in a sibling position does not satisfy the case. A flag-only option with an
inline value is rejected by the structural check. A generated minimum case
must provide values for required options and choose one option from each
required exclusive group. Repeating the selected member remains valid when
the built parser accepts it; add an explicit interaction when repetition has
separate product meaning. The checker does not execute `parse_args`, custom
converters/actions, or the handler. Route recognition is not proof that the
whole invocation is accepted. The linked behavior test must run the real CLI
invocation and prove the expected status, output, validation boundary, and
effects.
Each generated file is replaced atomically; if sync stops between the manifest
and spec replacements, `check` reports their mismatch.
Keep the review catalog, manifest, and spec as distinct files; sync and check
reject equal paths and symlink or hard-link aliases before writing.

Mark the behavior tests and register the shared assertion once at pytest
collection:

```toml
# pyproject.toml
[tool.pytest.ini_options]
markers = ["cli_case(case_id): links a behavior test to a reviewed CLI case"]
```

```python
# tests/conftest.py
from pathlib import Path

from cli_extended import assert_cli_case_tests, load_cli_review_catalog


def pytest_collection_finish(session):
    catalog = load_cli_review_catalog(Path("docs/cli-review.toml"))
    assert_cli_case_tests(session.items, catalog)
```

```python
import pytest


@pytest.mark.cli_case(
    "case:route:entrypoint:example/tool-deps/interaction:refresh-output/refresh-json"
)
def test_refresh_rejects_json():
    # Exercise the registered CLI and assert status, output, and effects.
    ...
```

The helper confirms that each exact node ID is collected and marked for its
case, and rejects unknown or inactive case markers. It cannot prove an
assertion is strong or that the test passed; the normal test gate must execute
these marked behavioral tests. One test may carry several `cli_case` markers.

CMRU's `tool-deps` is a useful first interaction example: declare
`--dry-run`/`--refresh`, then `--refresh` with `--json` and
`--allow-stale-tool-deps`; CMRU still owns the conditional rule and side-effect
assertions. Netcup can map `monitor-task show TASK_UUID --poll` to its parser
refusal test, and `show --json` / `watch --poll` to response-redaction and
polling tests. The converter's UUID checks are opaque runtime validation, not
missing CLI syntax. The CMRU and Netcup specs are examples only; adopting the
new catalog in either product is a separate change.

Use `OptionSpec` metadata for constraints argparse can express, such as
choices and required mutually exclusive alternatives. Conditional rules such
as “`--dry-run` requires `--update`” need an explicit refusal when the
condition is false; do not accept an option and then ignore it. Keep domain or
configuration-dependent checks in the consumer handler, and include the
condition in help and the semantic table. Override `VerbSpec.synopsis` only
when the actual syntax cannot be represented by the declared grammar, then
test both the displayed synopsis and accepted/rejected invocations.

Test effects at the owning boundary. A dry-run oracle should assert the
complete planned actions and that none of the mutations declared by the
product contract occurred. State separately whether read-only subprocesses,
network access, or credential reads are part of plan derivation. Confirmation
should follow validation and describe that same complete plan; `--yes` should
bypass only that confirmation. Exercise handlers through the registry so
tests receive the registered defaults. If a test calls a handler directly,
provide the complete namespace shape rather than adding fallback reads that
hide a mismatch between the handler and parser.

### Service entrypoints and side-effect-free discovery

For an executable that normally starts a daemon or other long-running process,
make its entrypoint parse the invocation before constructing that process.
Keep module-level imports and registry construction inert; a lightweight
handler can import the service implementation only when valid arguments reach
it. If the service's documented invocation has no verb, use
`CliRegistry(single_command=True, no_args_action=True)` rather than making
bare invocation accidentally start work. The parser still owns `--help`,
`--version`, and malformed-argument exits.

Prove the boundary through the installed executable, not only a parser unit
test. Use a startup sentinel or fake service factory and assert that help,
version, and invalid arguments do not start the service, read credentials,
connect to a provider, or create state. Assert that the valid no-argument
invocation reaches the service handler. Nyxloom found that its `nyxloomd
--help` path started the daemon before arguments were handled; see the
[command ownership guidance](../../../nyxloom/docs/CLI-REFERENCE.md#command-ownership).

### Package the same library revision that was tested

When a consumer bundles or vendors `cli-extended` from a sibling source tree,
verify each boundary independently: its package-discovery metadata must include
the library, the test lane must import the library from the selected worktree,
and the container build context must contain the source after `.dockerignore`
rules are applied. A successful local wheel build does not prove that a
Dockerfile can see the same files. Build the real wheel or image target,
inspect the artifact for the library package and console-script metadata, then
install and invoke it from outside the checkout without a repository
`PYTHONPATH`. Nyxloom's adoption review caught a root `.dockerignore` rule that
silently excluded the library source from its Docker wheel build; the fix and
acceptance evidence are recorded in its
[P112 report](../../../nyxloom/nyxloom-trove/reports/nyxloom-P112-REPORT.md).

In source-mode tests, point both the consumer and library imports at the same
selected worktree. Keep that path explicit rather than inheriting a `PYTHONPATH`
from the main checkout; otherwise the test can pass or fail against a different
library revision than the one being adopted.

Finally, test the built wheel from outside the source checkout. Invoke every
installed script and supported module CLI, and exercise bootstrap/project-step
entrypoints in the environment that actually uses them. Test generated
standalone tools in their own runtime. CMRU's [canonical CLI semantic audit](../../../cmru/docs/SPEC.md#s-cli9-canonical-cli-grammar-and-semantic-audit)
shows one way to keep the grammar inventory and product-owned behavior review
together without treating Python libraries as executable commands.

The `show`/`watch` example is the shape used by the Netcup task monitor. The
Netcup `scp-api.py`, `install-host.py`, and `monitor-task.py` entrypoints now
use `cli-extended` for parser setup, grouped help, common options, dispatch,
and error/cancellation handling while retaining provider-specific vocabulary
and safety policy. See the
[`Netcup quickstart`](../../../scripts/netcup/README.md) and
[`Netcup design guide`](../../../scripts/netcup/DESIGN-GUIDE.md) for their
current command-level workflows and the remaining domain-owned behavior.

The proposed task-monitor operation split is:

| Operation | Netcup command |
| --- | --- |
| Fetch one task snapshot | `show TASK_UUID`; add `--json` for the redacted JSON response |
| Poll until a terminal state | `watch TASK_UUID`; `--poll` belongs to a `STOP CONDITIONS` option group |
| Inspect secret-bearing response fields | `show TASK_UUID --json --debug-raw`; this prints a warning and disables response redaction |
| Cancel a task | keep under `scp-api.py tasks cancel`, which owns API mutations |

On adoption, bare invocation should print generated help, and help/version
discovery must not load credentials or API settings. The consumer validates
UUID syntax and the API's top-level response type, but retains ownership of
Netcup task fields and terminal-state meaning. Do not add a separate `check`
verb for a one-fetch preflight: `show` already provides that operation.

The registry uses one width-aware formatter for generated top-level and
command-specific help. Terminal output follows the detected terminal width,
with a 120-column fallback, while generated Markdown is a full reference
format rather than terminal output pasted into a document. Structured
choice/default attributes are rendered there too; keep ordinary argparse
options in `OptionSpec` so they are not lost from generated docs.

### Color and terminal presentation

The normal `app.run()` boundary applies one color policy; consumers should not
write ANSI escapes, add Colorama just for CLI output, or recolor the library's
severity tags themselves:

| Invocation/output | Behavior |
|---|---|
| No explicit switch | Color only when the stream receiving the text is a TTY and `NO_COLOR` is unset |
| `--color` | Force color, including when output is redirected or `NO_COLOR` is set |
| `--no-color` | Disable color |
| Both switches | Refuse the invocation; do not let argument order decide |

This covers generated terminal help (including error help), severity tags,
hints, and the shared progress renderer. Help color detection uses stdout;
diagnostics/progress use stderr. `app.catalog.render(output_format="markdown")`,
version output, `runtime.output.primary()` results, and JSON remain plain so
they can be copied, piped, or parsed. Use `runtime.output.info()`, `warn()`,
`error()`, and `hint()` for human diagnostics; keep domain result tables
uncolored unless the product has a separately documented, tested rendering
contract.

If a consumer owns a styled primary result, query the same stream-specific
policy instead of repeating TTY/`NO_COLOR` checks:

```python
color = (
    not runtime.json_mode
    and runtime.output.color_enabled(runtime.output.stdout)
)
render_result(result, color=color)
```

`color_enabled(stream)` reports the CLI policy; it does not sanitize a
consumer's renderer. The consumer must keep JSON and other machine output plain
even when `--color` is explicit. Pass stdout for a primary result and stderr
for a diagnostic renderer; the two streams can have different TTY state.

The standard recipe is simply to let `RegisteredCli.run()` own dispatch and
rendering:

```python
app = cli.build()
raise SystemExit(app.run(expected_exceptions=(OSError,)))
```

Do not print `app.parser.format_help()` as the executable's help path: that
method returns plain text for introspection and documentation tooling, while
`app.run()` applies the invocation's terminal and color policy.

Bare invocation prints help by default. Set `no_args_action=True` only when
the command's explicitly documented purpose is to act with no arguments; it
is restricted to the single-command registry form, and `--help` must remain
side-effect free. The ordinary `assert_cli_contract()` helper assumes bare
invocation is help, so do not use it unchanged for that deliberate exception.

## Interactive data collection

For a multi-step wizard, collect values with `runtime.prompts`. The shared API
offers `text`, `password`, `confirm`, `select`, and `checkbox`; it checks both
injected stdin and stdout before loading or calling a driver. The default
Questionary driver is imported only on the first prompt. Install it with
`python3 -m pip install 'cli-extended[interactive]'`, or let a vendored product
name its own optional extra through `app.run(interactive_extra="product[interactive]")`.
Help, version, and non-interactive commands do not require the extra.

The prompt layer returns values. The handler owns schema validation,
confirmation timing, file writes, and rollback. Cancellation raises
`PromptCancelled`; an uncaught cancellation reaches the CLI boundary as exit
status `130`. Password prompts are hidden and register their answers for
ordinary output redaction.

Tests can inject a `PromptDriver` without depending on Questionary or a real
terminal. TTY-marked streams still exercise the production policy:

```python
import io

from cli_extended import CliIdentity, CliRegistry, PromptDriver, VerbSpec


class TestTTY(io.StringIO):
    def isatty(self):
        return True


class FixedPrompts:
    def text(self, message, *, default=None, required=True):
        return "demo-project"

    def password(self, message, *, required=True):
        return "secret-for-test"

    def confirm(self, message, *, default=False):
        return True

    def select(self, message, choices, *, default=None):
        return choices[0]

    def checkbox(self, message, choices, *, default=()):
        return list(default)


test_driver: PromptDriver = FixedPrompts()


def configure(_args, runtime):
    name = runtime.prompts.text("Project name")
    mode = runtime.prompts.select("Mode", ("safe", "full"))
    # The application validates name/mode and writes its own config here.
    runtime.output.primary(f"prepared {name} in {mode} mode")
    return 0


identity = CliIdentity("EXAMPLE", "1.0.0", "Example", command="example")
registry = CliRegistry(identity, prog="example", description="Configure example.")
registry.register(VerbSpec("configure", description="configure", handler=configure))
status = registry.build().run(
    argv=["configure"],
    prompt_driver=test_driver,
    stdin=TestTTY(),
    stdout=TestTTY(),
    stderr=io.StringIO(),
)
```

Implement all five `PromptDriver` methods when a test flow uses other prompt
kinds. A driver receives the same defaults and choices as the runtime; it must
return a declared choice, and it may return `None` to signal cancellation.
Cancellation is distinct from valid answers such as `False` or an empty
checkbox list. Prompt collection must not write state or decide whether
collected values satisfy the product schema.

## Ship your agent skills

Skills that live in a repository's `.claude/skills/<name>` are only visible to
people working in that checkout. To make them follow the installed tool (the
version always matches the wheel), move them into the package and register the
shared verb group.

1. Move each skill: `git mv .claude/skills/<name> src/<pkg>/skills/<name>`.
   The frontmatter must satisfy [SPEC §14](../SPEC.md#14-packaged-agent-skills)
   (single-line `name`/`description`, `name` equal to the directory name).
2. Ship it as package data in `pyproject.toml`:

   ```toml
   [tool.setuptools.package-data]
   "example_tool" = ["skills/**/*"]
   ```

3. Register once, on the same registry as your other verbs:

   ```python
   from cli_extended import CliIdentity, CliRegistry, register_skills_verbs

   identity = CliIdentity.resolve(
       name="EXAMPLE", command="example", long_name="Example Tool",
       distribution="example-tool",
   )
   registry = CliRegistry(identity, prog="example", description="Example tool.")
   register_skills_verbs(registry, package="example_tool")
   ```

4. Add `example skills install` to the mdt or devcontainer finalize step, and
   `example skills check` to the tool's doctor or CI. Re-run `install` after
   every upgrade: wheels cannot run post-install hooks.
5. For a project-level install (skills committed or mounted for one checkout
   only) use `example skills install --dest <project>/.claude/skills`;
   `--dest` is exactly that directory and cannot be combined with `--harness`.
   Use `--dry-run` first to see the plan.

A skill you edited locally is reported `modified` and kept until you pass
`--overwrite-modified`. Directories created by another tool, or by hand, are
never replaced; remove them yourself if they are no longer wanted.

## Add a `doctor` verb

Replace a hand-rolled `doctor` with the shared one. Declare each probe as a
`DoctorCheck`; the verb adds `--check NAME`, `--json`, exit codes and crash
handling.

```python
import shutil

from cli_extended import (
    CheckResult, CliIdentity, CliRegistry, DoctorCheck,
    register_doctor, register_skills_verbs,
)


def docker_check(runtime, args) -> CheckResult:
    if shutil.which("docker") is None:
        return CheckResult("fail", "docker not found on PATH",
                           remedy="install docker or add it to PATH")
    return CheckResult("ok", "docker found", details={"path": shutil.which("docker")})


def cache_check(runtime, args) -> CheckResult:
    return CheckResult("warn", "cache is empty", remedy="run 'example warm'")


identity = CliIdentity("EXAMPLE", "1.2.3", "Example Tool", "example")
registry = CliRegistry(identity, prog="example", description="Example tool.")
register_doctor(registry, [
    DoctorCheck("docker", "docker is installed", docker_check),
    DoctorCheck("cache", "cache is warm", cache_check),
])
# Optional, in either order: adds the automatic `skills` check.
register_skills_verbs(registry, package="example_tool")
```

A check can read its own options. Pass them with
`register_doctor(registry, checks, options=[OptionSpec(("--helper-image",),
"helper image", metavar="IMAGE")])` and use `args.helper_image` inside
`run(runtime, args)`.

`example doctor`, `example doctor --check docker --json` and CI use the same
exit code (1 only for `fail`). Do not name a check `skills`.

Mapping the existing doctors:

- **cgprofile** (`scripts/cgroup-profiler/cgprofile.py` `cmd_doctor`): prints an
  `access` key/value table, a reporting-venv state and a resolved mode, and
  returns 1 only when the helper spec cannot be built. Express it as checks
  `access` (key/value table into `details`), `reporting-venv` (`ok`, `warn` for
  "present but this interpreter lacks the libraries", `fail` for missing with
  remedy `run ./setup.sh`) and `mode` (`fail` on `AccessError`, helper image and
  mounts in `details`). Its `--helper-image` and `--helper-cgroup-parent`
  options now fit: pass them as `options=[OptionSpec(("--helper-image",), ...),
  OptionSpec(("--helper-cgroup-parent",), ...)]` and read
  `args.helper_image` / `args.helper_cgroup_parent` in the `mode` check.
- **nyxloomctl** (`cli_registry.py` `doctor` and `route doctor`): the project
  doctor yields findings with severities `critical`, `error`, `warn`. Map
  `critical`/`error` to `fail` and the rest to `warn`, one check per finding
  kind or per project, and put the finding rows in `details`. Its options
  (`--project-id`, `--rebuild`, `--write`, `--liveness`) and the `route doctor`
  `--no-probe` are passed as `options=[...]` and read from `args`; a
  `--liveness` flag is read by the checks that should run only in that mode
  (return `skip` otherwise), or use `--check liveness`. `route doctor` is
  registered on its own sub-registry with `--no-probe`. Option flags may not
  reuse `--check` or a library control such as `--json` (`ValueError`).

## Consumer responsibilities

| `cli-extended` guarantees | The adopting CLI must decide and implement |
|---|---|
| identity formatting, `help`/`version`, bare invocation, width-aware grouped help | authoritative version source, product identity, verbs, groups, examples, and valid workflows |
| parser registration and generated parser/dispatch/help consistency | API/config/file/domain validation and all closed vocabulary values |
| common diagnostics, severity levels, colour controls, stdout/stderr policy, JSON-mode progress handling | result schemas, tables, pagination/truncation semantics, and provider-specific progress events |
| common `--yes` option by default for registrations marked `mutating`; `confirmation_required` controls that requirement while preserving the mutation label, and `include_confirmation` remains a compatible alias; default-no confirmation helper | decide exactly what changes, validate before prompting, and prompt immediately before the mutation |
| optional `runtime.prompts` collection API, Questionary adapter, terminal checks, cancellation type, and injectable `PromptDriver` | domain validation, schema conversion, persistence, and whether an answer authorizes a product action |
| clean Ctrl-C and concise failures for declared expected exceptions | classify expected domain exceptions; unexpected bugs must remain visible as tracebacks |
| redaction of explicitly registered secrets in output, logging, JSON results, prompts, and progress | identify/provide secret values and avoid leaking them through external subprocesses, files, or messages emitted outside the helper |
| scoped standard-library logging for the configured logger namespace | put application loggers under that namespace or configure `logging_logger`; retain useful log calls and classify secret-bearing data |
| a black-box help/version/parse-error contract assertion | launch the real executable in tests and separately prove help/version made no API, credential, or filesystem side effects |

`--yes` is not a safety policy. A handler must finish target/config/API
validation before asking for consent. The helper cannot decide whether a
Netcup firewall update, server reinstall, database migration, or deployment is
safe, and it does not bypass deny-lists or other domain guards.
When an explicit product safeguard is the consent boundary, declare the verb
as `mutating=True, confirmation_required=False`; this preserves the mutation
label without advertising a generic `--yes` that does not authorize the
operation. Test both that `--yes` is rejected and that a failed role or state
guard leaves the product unchanged.
Use `runtime.output.is_interactive` when deciding whether to offer a multi-step
flow before entering it. `runtime.prompts` enforces the same requirement before
loading or calling a driver, using both injected stdin and stdout.

Expected failures should be passed as specific types to `app.run()`:

```python
raise SystemExit(
    app.run(
        expected_exceptions=(NetcupAPIError, OSError),
        secrets=(refresh_token,),
    )
)
```

Do not pass `Exception` as an expected type. That would hide programming bugs
as ordinary operator errors. Raise `CliFailure` for a known refusal that needs
a concise message, exit status, or hint. Keep validation messages actionable
and truthful.

### Logging namespace

`CliRegistry` scopes Python logging to `identity.command_name` by default and
restores the logger when the handler exits. Application module loggers should
be named under that namespace, e.g. `example.api`; otherwise set
`logging_logger="example"` on the registry. The common `--log-level` policy
then filters ordinary `logging` records. Avoid configuring the process-wide
root logger in a library module.

### JSON and progress

Primary machine output goes to stdout; diagnostics and human progress go to
stderr. When `--json` owns stdout, `--progress=rawjson` is intentionally muted;
`plain`/`tty` progress remains on stderr. `ProgressRenderer` and
`runtime.progress()` redact the secrets passed to `app.run()` unless
`--debug-raw` was explicitly requested. Consumers still own the JSON schema
and must not print ad-hoc progress directly to stdout.

## Tests required for an adoption

Use `assert_cli_contract()` against the real executable/subprocess for bare
invocation, both help/version spellings, rejection of `-h` (unless an existing
compatibility promise is documented), every registered verb's paired help,
and selected invalid invocations. For each selected known-verb failure, pass
`known_verb_errors={label: verb}` so the helper checks that the complete
verb-specific help accompanies the diagnostic, not just a usage synopsis.
The failure diagnostic is the first block; a blank line then separates it from
the product identity and complete help. This keeps the cause easy to spot while
preserving the identity-headed help document.
Also test facts the helper cannot observe:

- no credentials, API calls, file changes, or mutations on help/version paths;
- real handler dispatch and each verb's parser options/actions;
- confirmations occur after validation, default to refusal, and `--yes` skips
  only that prompt;
- all five prompt methods return the declared value types; invalid choices,
  empty required input, Escape, Ctrl-C, non-TTY refusal, lazy dependency
  loading, and password redaction are covered;
- API/config failures are concise while unexpected exceptions retain tracebacks;
- JSON stdout remains parseable, including progress and error cases;
- known secrets are absent from normal diagnostics, logging, JSON, and progress;
- Ctrl-C at prompts and long-running operations exits `130` without traceback.

Package gate (R0/R1/R2 plus the independent R3 canary):

```bash
cd libraries/cli-extended
./run-gate.py gate
```

All gate lanes execute in `tester-unified`. R1 enforces 100% statement and
branch coverage over every shipped `cli_extended` module. R3 deliberately
breaks JSON redaction in a disposable copy and requires the focused regression
test to reject it. Ruff remains a separate static check: `ruff check src tests`.

## Adopting cli-extended end to end

The ordered steps a tool follows. Each ends in something `cli-extended audit`
can check or an agent can judge from the
[adoption checklist](ADOPTION-CHECKLIST.md) (row ids in brackets).

1. **Dependency** [AC-24, AC-25]. Declare the released library with a floor and
   a reason; never vendor it or put its checkout on a path.

   ```toml
   [project]
   dependencies = [
       "cli-extended>=0.2.0",  # audit verb and adoption skill
   ]
   ```

2. **Identity** [AC-01, AC-02]. One resolver, no fallback; see
   [Install and choose a version source](#install-and-choose-a-version-source).

   ```python
   IDENTITY = CliIdentity.resolve(
       name="EXAMPLE", long_name="Example tool", command="example",
       distribution="example-tool",
   )
   ```

3. **Runtime policy** [AC-10, AC-11]. Report unexpected exceptions and raise
   `CliFailure` for domain errors.

   ```python
   registry = CliRegistry(
       IDENTITY, prog="example", description="Example tool.",
       unexpected_exceptions="report",
   )
   ```

4. **Registration** [AC-03, AC-04, AC-09]. Declare verbs once; see
   [Turn an interface inventory into registrations](#turn-an-interface-inventory-into-registrations).
5. **Dry-run and confirmation** [AC-12, AC-13]. Mark mutating verbs
   `mutating=True, dry_run=True`; see
   [Replacing hand-rolled version lookup, exception wrappers, and --dry-run](#replacing-hand-rolled-version-lookup-exception-wrappers-and---dry-run).
6. **Constraints** [AC-05, AC-06, AC-07]. Declare conflicts and requirements
   instead of checking in handlers; see
   [Replacing handler-side option checks and name-list parsing](#replacing-handler-side-option-checks-and-name-list-parsing).
7. **Surface lifecycle and LLM review** [AC-16, AC-17, AC-18]. Configure
   `review`, `manifest` and `spec`, then run the loop in
   [The review loop](#the-review-loop): `cli-extended surface sync`, `pack`,
   judge, `check`.
8. **Skills** [AC-19]. Package skills and register the `skills` verbs; see
   [Ship your agent skills](#ship-your-agent-skills).
9. **Doctor** [AC-20]. Register a `doctor` verb when the tool has an
   environment to verify; see [Add a `doctor` verb](#add-a-doctor-verb).
10. **Tests** [AC-21, AC-22, AC-23]. Follow
    [Tests required for an adoption](#tests-required-for-an-adoption).
11. **Audit** [all rows]. Run the audit, fix every `fail`, and let the
    `cli-extended-adoption` skill judge the `manual` items.

    ```bash
    cli-extended audit --json
    cli-extended skills install --harness claude   # installs cli-extended-adoption
    ```

    Exit status is 1 only when an item fails. Text lines read
    `[FAIL] AC-05 shadowed-controls: 1 consumer option(s) shadow library controls`,
    followed by indented `evidence:` and `remedy:` lines.
