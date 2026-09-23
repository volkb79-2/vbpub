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
  attributes such as `action`, `choices`, or `default`, and structured
  required/exclusive-group metadata.

`CliRegistry` turns those declarations into argparse parsers, grouped terminal
help, full Markdown reference help, a dispatch map, and common shell options.
The registry's `description` is the overall CLI purpose shown after usage in
the top-level catalog; it is distinct from each verb's `description`.
It also checks duplicate command names and catalog/parser consistency. The
consumer should not separately hand-maintain a command list, parser map, and
help list.

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

Place options according to when they can be used: invocation-wide options
that must precede a verb belong in `CliRegistry.global_options`; command-local
options belong in that verb's `options`. Give every option an intentional
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

## Consumer responsibilities

| `cli-extended` guarantees | The adopting CLI must decide and implement |
|---|---|
| identity formatting, `help`/`version`, bare invocation, width-aware grouped help | authoritative version source, product identity, verbs, groups, examples, and valid workflows |
| parser registration and generated parser/dispatch/help consistency | API/config/file/domain validation and all closed vocabulary values |
| common diagnostics, severity levels, colour controls, stdout/stderr policy, JSON-mode progress handling | result schemas, tables, pagination/truncation semantics, and provider-specific progress events |
| common `--yes` option only for registrations marked `mutating`; default-no confirmation helper | decide exactly what changes, validate before prompting, and prompt immediately before the mutation |
| clean Ctrl-C and concise failures for declared expected exceptions | classify expected domain exceptions; unexpected bugs must remain visible as tracebacks |
| redaction of explicitly registered secrets in output, logging, JSON results, prompts, and progress | identify/provide secret values and avoid leaking them through external subprocesses, files, or messages emitted outside the helper |
| scoped standard-library logging for the configured logger namespace | put application loggers under that namespace or configure `logging_logger`; retain useful log calls and classify secret-bearing data |
| a black-box help/version/parse-error contract assertion | launch the real executable in tests and separately prove help/version made no API, credential, or filesystem side effects |

`--yes` is not a safety policy. A handler must finish target/config/API
validation before asking for consent. The helper cannot decide whether a
Netcup firewall update, server reinstall, database migration, or deployment is
safe, and it does not bypass deny-lists or other domain guards.
For a multi-step prompt flow, use `runtime.output.is_interactive`; it requires
both input and output to be TTYs and works with the runtime's injectable test
streams.

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
