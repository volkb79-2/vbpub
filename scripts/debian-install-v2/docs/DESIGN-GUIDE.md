# Debian install v2 CLI and wizard design

This note explains why the operator interface is shaped as it is. The
supported commands and copyable recipes are in
[`CONSUMERS.md`](CONSUMERS.md); the product feature surface is in
[`../README.md`](../README.md).

## Verbs represent operations

The CLI exposes `wizard`, `install`, `resume`, `status`, `verify`, `plan`,
`disable-stage2`, and `build-customscript` as verbs rather than one `--action`
option. Each operation has its own help, accepted options, risk declaration,
examples, and handler. Shared parser behavior—identity/version output, grouped
help, diagnostics, `--yes`, `--debug`, output modes, progress behavior, and
Ctrl-C handling—comes from `libraries/cli-extended`.

The top-level catalog uses the registry's overall CLI description to explain
the workflow, then lists verb names with aligned short summaries. Full argument
and config-source syntax stays in per-verb help, where it can be read alongside
the corresponding option descriptions without crowding the operation map.

This prevents unrelated options from appearing valid for every operation and
lets help show the workflow directly. `install` is destructive and requires a
confirmation unless `--yes` is supplied; `--dry-run` records planned actions
without executing host commands. `--yes` accepts the operation’s confirmation,
not missing settings or wizard answers.

## Configuration has one authority

`Config` plus `load_config()`/`validate_config()` in `config.py` is the source
of truth for accepted fields, defaults, and validation. The wizard describes
which fields belong together and how to prompt for them; it does not re-create
the schema in a separate form model. It builds a candidate `Config`, runs it
through the same loader, and only writes a validated result.

`save_config()` writes atomically with mode `0600`, refuses symlink targets,
and does not overwrite an existing file unless the operator confirms that
write. Secret fields are hidden while prompting and replaced with a status
marker in the summary. A `--from-config` file is validated before it can seed
the wizard.

The wizard is section-oriented so operators can keep the existing settings
they do not want to revisit. All mutable `Config` fields are covered by
prompt metadata; only `schema_version` and the currently required
`fresh_install=true` are fixed by this v2 release.

Every install/config-inspection verb requires one explicit settings source:
`--config FILE` or `--config-json JSON`. There is no implicit target config to
mistake for the intended host. These alternatives are declared as one required
mutually-exclusive group in `cli-extended`, so parser enforcement, error help,
and the displayed synopsis all come from the same metadata rather than a
hand-maintained usage string.

## Prompt library boundary

The prompt package is an optional dependency and is imported only by the
`wizard` verb. `questionary` supplies terminal interactions such as grouped
choices, confirmations, text fields, and hidden password entry. The installer
does not depend on it for help, config loading, planning, installation, or
remote execution. If it is absent, the wizard reports the precise optional
install command; the rest of the CLI remains usable.

Questionary is preferred here over building another prompt framework: the
interaction is conventional and relatively small, while validation, defaults,
secret handling, cancellation, and file safety remain owned by this project.
Those decisions are product behavior and should not be delegated to a UI
library. The dependency is pinned in the installer project's
`wizard-requirements.txt` so the reusable CLI library does not depend on an
interactive UI package and prompt API changes are deliberate rather than
silently changing installation flows.

## Remote bootstrap stays self-contained

`build-customscript` belongs to this project because it serializes this
installer’s validated `Config` and the matching remote bootstrap command. It
does not speak a hosting provider’s API. `install-host.py` and other provider
clients can consume the generated JSON without learning Debian installer
internals.

The remote bootstrap download includes the installer and the same
stdlib-only `cli_extended` runtime in one archive. The target needs no pip
installation and no live checkout of the controller’s repository. Questionary
is not bundled because remote installation does not run the wizard. The
generated custom-script launcher fetches the first bootstrap file through
Python’s standard-library HTTPS client and reports download failure with a
nonzero exit, rather than relying on shell-specific pipeline semantics.

## Terminal color remains a shared presentation concern

The installer registers commands and routes output through `cli-extended`;
it does not add its own ANSI strings or color package. The shared boundary
styles generated terminal help and diagnostic severity tags according to the
destination TTY, `NO_COLOR`, `--color`, and `--no-color`. Config, status/plan
results, and JSON stay plain. This keeps copied configuration and redirected
output stable while allowing terminal help/errors to be readable at a glance.

Generated bundles can contain credentials if the chosen configuration does.
The normal output path therefore uses the shared CLI’s secret redaction and
refuses to emit a secret-bearing bundle unless `--debug-raw` is explicitly
requested. Operators who opt in must protect the destination and any provider
configuration/API record that stores the custom script.
