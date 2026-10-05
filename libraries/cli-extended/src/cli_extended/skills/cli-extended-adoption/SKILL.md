---
name: cli-extended-adoption
description: Use when adopting or auditing cli-extended in a CLI project; runs `cli-extended audit`, fixes every failure, judges every manual item and records the outcome as adoption findings.
---

# Adopt cli-extended in a project

`cli-extended audit` does the mechanical checks. You do the judgement. Do not
skip a `manual` item: each one needs a decision recorded in the findings file.

## Procedure

1. Run `cli-extended audit --json` from the project (pass `--config PATH` or
   `--cli ID` when needed). Exit 2 means the project configuration or factory
   could not load; fix that first. Items marked `heuristic:` are text scans and
   can be wrong; confirm them by reading the named files.
2. Fix every item with status `fail`, following its `remedy` and `evidence`.
   Re-run the audit until no item fails.
3. For every `warn` item, either fix it or record a `wontfix` finding with a
   rationale.
4. For every `manual` item, judge it with the criteria below. Record each
   judgement as a finding in the findings file, by hand:
   `category = "adoption"`, `status = "open"` when something must change (with a
   concrete `remedy`), or `status = "wontfix"` with a `rationale` when the
   current state is right. A finding needs `id`, `status`, `severity`,
   `category`, `summary`, `remedy` and, for `wontfix`, `rationale`; `severity`
   is one of `blocker`, `major`, `minor`, `note`. The library never writes this
   file.
5. Also judge the checklist areas the audit cannot see: a project that
   hand-rolls argparse registration, handler-side option checks, name-list
   parsing, `print` plus `return 1` error handling, its own prompts, or its own
   progress output has not finished adopting. Record those the same way.
6. Run the `cli-extended-review` procedure (sync, pack, judge, edit the review
   catalog, check) to review the surface itself.

## Decision criteria for manual items

- `version-source`: the tool's version must come from
  `CliIdentity.resolve(...)` (installed metadata and/or a checked-in VERSION
  file, never a literal fallback). Open a finding if the version is read any
  other way.
- `synopsis-overrides`: keep a hand-written `synopsis` only when the derived
  usage line would mislead (for example a complex alternative the declared
  options cannot express). Otherwise open a finding to delete it.
- `configure-callbacks`: a `configure` callback is legitimate only for syntax
  the declarative specs cannot express. If it adds plain positionals, options,
  or conflicts and requirements between options, open a finding to move it to
  `ArgumentSpec`, `OptionSpec` and `Requires`/`Conflicts`/`RequiresChoice`.
- `hidden-options`: a hidden option must be internal plumbing or deprecated with
  a removal plan. Public behaviour that is merely undocumented is a finding.
- `mutation-safety`: a verb that mutates state needs either `--dry-run`
  (`VerbSpec(dry_run=True)`) or confirmation. `wontfix` only when the verb is
  idempotent, cheap to undo, and the rationale says so.
- `skills-packaged`: decide whether agents drive this tool. If yes, the tool must
  ship skills as package data and register `register_skills_verbs`; if no,
  record `wontfix` with that reason.
- `doctor`: decide whether the tool has an environment to verify (dependencies,
  services, credentials, paths). If yes, a `doctor` verb is required; if no,
  record `wontfix`.
- `pytest-plugin`: when a review catalog exists, tests must run through the
  `cli_extended.pytest_plugin` plugin so every active review case is linked to a
  real test. Without a catalog there is nothing to link.
- `dependency-declared`: scripts that are not packaged use the installed library
  and need no pyproject dependency; a packaged tool must declare
  `cli-extended>=X.Y.Z` with a reason for the floor.
- `surface-check`: manual only when the surface is not configured. A tool with
  more than one verb needs a surface manifest, spec region and review catalog;
  a single trivial verb may record `wontfix`.

## Rules

- Never silence an item by editing the audit's inputs (deleting a verb, hiding
  an option) without fixing the underlying behaviour.
- Judge from the code and the checklist criteria above, not from the item's
  summary alone; read the files named in `evidence`.
- Never delete a finding to make a check pass. Close it with `fixed` after the
  fix is in the code and the audit agrees.
- Edit only the findings file, the review catalog and project sources; generated
  files change through `cli-extended surface sync`.
