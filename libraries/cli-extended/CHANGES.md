# Changelog

All notable changes to cli-extended are recorded here. Entries marked
`cmru: generated` are produced from the project-scoped release range before the
release gate runs. The hand-written section below carries what a consumer must
know when upgrading; it is not generated.

## Contract and upgrade notes

### 0.2.0 — first released wheel (unified adoption)

cli-extended is now a released wheel (`cli_extended-<version>-py3-none-any.whl`)
published on GitHub Releases under `cli-extended-v<version>`, with a moving
`cli-extended-latest/latest.json` pointer (`project`, `version`, `tag`, `asset`,
`sha256`, `url`). It is never published to PyPI: install it with
`pip install --no-index --find-links <dir-with-the-wheel> cli-extended`. Releases
stay on `0.x` until every vbpub CLI has adopted the library; `1.0.0` follows.

**Library contract version 1.** `contract.CONTRACT_VERSION = 1` names the set of
library-owned common controls (`--help`, `--version`, `--json`, `--yes`,
`--dry-run`, `--traceback`, `--color`/`--no-color`, `--quiet`, `--debug`/
`--verbose`, `--log-level`, `--progress`, `--debug-raw`). The surface manifest
(schema 7) records `library_contract` and per-route `common_controls`;
consumer review signatures cover only consumer grammar (the four reviewed
controls `--json`, `--yes`, `--debug-raw`, `--dry-run` keep their candidates
with a flag-and-route-only signature). Upgrading the library without a
contract bump leaves every consumer signature unchanged; a bump produces
exactly one `surface check` finding pointing here, not a re-review of every
route. Upgrading from a pre-0.2.0 manifest: run `cli-extended surface sync`
once. See SPEC "Library contract and contract version".

**Verb options now default to `None`/`False` instead of being absent.** A
`VerbSpec` option without an explicit `default` used to leave `args.<dest>`
unset when omitted (handlers crashed with `AttributeError`); it now has
argparse's own default. A destination shared with a global option or library
control still suppresses its default so a value parsed before the verb
survives. The surface manifest records these defaults, so manifests and the
review signatures of routes with such options change: run
`cli-extended surface sync` and re-review the cases `check` reports as
changed. `CONTRACT_VERSION` is not bumped: no library control changed, and
this lands before contract version 1 is first released, so it is part of the
version-1 baseline (SPEC "Library contract and contract version", rule 4).

When a future release bumps `CONTRACT_VERSION`, its entry below states what
changed in the controls and what a consumer must re-review.

New in this release (each documented in README, SPEC and docs/CONSUMERS.md):

- `CliIdentity.resolve(...)` — one version source (installed metadata and/or a
  VERSION file, which must agree), no literal fallback.
- `CliRegistry(expected_exceptions=..., unexpected_exceptions="report")` and the
  library `--traceback` control.
- `VerbSpec(dry_run=True)` — library `--dry-run`; `runtime.confirm()` returns
  False under it and prints `Dry run: no changes made.`.
- Declarative option constraints `Requires`, `Conflicts`, `RequiresChoice`, and
  the `SelectorList` value type.
- `register_skills_verbs(...)` — packaged agent skills with install, uninstall,
  check and list, frontmatter stamping, a banner and `.cli-extended-stamp.json`.
  `RegisteredCli.skills_package` carries the registration.
- `register_doctor(...)` — a shared `doctor` verb over named checks.
- Project configuration in `cli-extended.toml` or `[tool.cli-extended]`, and the
  `cli-extended` console script: `surface sync|check|template|pack|report`,
  `audit`, `skills`.
- The surface review workflow: `surface pack` plus the packaged
  `cli-extended-review` skill and review rubric, a findings file, and
  `surface check` refusing open blocker/major findings.
- `docs/ADOPTION-CHECKLIST.md` (AC-01..AC-25), `cli-extended audit`, and the
  packaged `cli-extended-adoption` skill.
- Test helpers `invoke_script`, `invoke_module`, `make_invoker` (mandatory
  absolute `home`; `HOME`/`XDG_*_HOME` cannot be overridden) and the opt-in
  `cli_extended.pytest_plugin` (`cli_case` marker; strict by default).

Behaviour changes a consumer can see:

- **Colour.** On Pythons whose `argparse` colours its own help (3.14+), that
  native palette is disabled; every colour comes from the cli-extended policy.
  On a TTY the help palette changes.
- **Help width.** `surface pack` renders help at a fixed 100 columns
  (`fixed_help_width`), so packs are byte-stable across terminals.
- **Deprecation.** `python -m cli_extended.surface_cli` still works, keeps its
  flags, and first writes `[WARN] ... is deprecated` to stderr. Use
  `cli-extended surface ...`; the shim is removed in a later release.

<!-- cmru: release history -->
