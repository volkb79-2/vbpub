# cli-extended consumer guide

`cli-extended` standardizes CLI mechanics; it is not an application framework
and does not own product behavior. The adopting CLI declares its public
interface once with `CliRegistry`, then supplies domain-specific parsers and
handlers only where needed. The package's
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

- `VerbSpec`: name, synopsis, user-facing description, semantic group,
  examples, behavior attributes, handler, positional arguments, and options;
- `ArgumentSpec`: positional name, metavar, description, and argparse
  attributes such as `nargs`, `choices`, or `type`;
- `OptionSpec`: flags, display metavar, help group, description, and argparse
  attributes such as `action`, `choices`, `default`, or `required`.

`CliRegistry` turns those declarations into argparse parsers, grouped terminal
help, full Markdown reference help, a dispatch map, and common shell options.
It also checks duplicate command names and catalog/parser consistency. The
consumer should not separately hand-maintain a command list, parser map, and
help list.

Use `VerbSpec.configure(parser)` only for genuinely custom structures—for
example, nested positional actions or a mutually exclusive argument group.
Keep common simple positionals/options in the structured `arguments` and
`options` fields so they appear in generated Markdown too.

Place options according to when they can be used: invocation-wide options
that must precede a verb belong in `CliRegistry.global_options`; command-local
options belong in that verb's `options`. Give every option an intentional
display group (`OUTPUT`, `FILTERS`, `STOP CONDITIONS`, etc.) instead of grouping
by implementation detail. Use `VerbSpec.group` for the semantic top-level
verb group. The same metadata drives terminal and Markdown help; avoid a
parallel manually formatted epilog for ordinary options. Custom parser
callbacks are an escape hatch for syntax argparse cannot express through the
structured fields, not the default registration path.

The generated common output/debugging controls are supported before and after
the selected verb. Keep their semantics independent of placement: for example,
`tool --quiet status` and `tool status --quiet` must both work, while combining
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

For the Netcup task monitor, the migration split is therefore:

| Current behavior | Verb-oriented interface |
| --- | --- |
| bare UUID polls until terminal | `watch TASK_UUID`; `--poll` belongs to a `STOP CONDITIONS` option group |
| `--json` fetches once and exits | `show TASK_UUID --json` |
| `--json --raw` includes secret-bearing response fields | `show TASK_UUID --json --debug-raw`, with the shared warning and explicit redaction opt-out |
| `--dry-run` only fetches once to prove the task exists | `show TASK_UUID`; add `check` only if it establishes a stronger, separately useful preflight guarantee |
| cancel a task | keep under `scp-api.py tasks cancel`, which owns API mutations |

This keeps presentation choices such as JSON from becoming verbs, while making
the actual one-shot and polling operations explicit.

The registry uses one width-aware formatter for generated top-level and
command-specific help. Terminal output follows the detected terminal width,
with a 120-column fallback, while generated Markdown is a full reference
format rather than terminal output pasted into a document. Structured
choice/default attributes are rendered there too; keep ordinary argparse
options in `OptionSpec` so they are not lost from generated docs.

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
invocation, both help/version spellings, every registered verb's paired help,
and selected invalid invocations. Also test facts the helper cannot observe:

- no credentials, API calls, file changes, or mutations on help/version paths;
- real handler dispatch and each verb's parser options/actions;
- confirmations occur after validation, default to refusal, and `--yes` skips
  only that prompt;
- API/config failures are concise while unexpected exceptions retain tracebacks;
- JSON stdout remains parseable, including progress and error cases;
- known secrets are absent from normal diagnostics, logging, JSON, and progress;
- Ctrl-C at prompts and long-running operations exits `130` without traceback.

Package self-tests:

```bash
PYTHONPATH=libraries/cli-extended/src pytest -q libraries/cli-extended/tests
ruff check libraries/cli-extended
ruff format --check libraries/cli-extended
```
