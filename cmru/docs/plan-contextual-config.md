# Plan: contextual CMRU configuration and positional project selection

Status: design only. This plan does not change release behavior and does not
authorize a release or publication.

## What the scan found

CMRU currently chooses configuration from the current directory only:

1. `<cwd>/cmru.toml` wins.
2. Otherwise `<cwd>/cmru.orchestration.toml` is selected.
3. No ancestor search occurs (`cmru/src/cmru/cli.py::_default_config_path`).

Loading a project document synthesizes a one-project orchestration view, but
uses that project directory as the secret root. Loading
`cmru.orchestration.toml` instead loads every registered project, applies
`[orchestration.defaults.env]`, and reads the sibling CMRU-root
`cmru.secret.toml`, with a project-local secret overlay.

The project documents currently repeat the same repository facts in
`[github]` and `[targets]`. The orchestration loader verifies that every
project repeats identical values, then takes the first project's values as the
estate values. This is duplicated source data and is the right candidate for
centralization.

The affected configuration and selection paths are:

| Surface | Current behavior | Contextual behavior |
|---|---|---|
| `status`, `release` | all projects from orchestration; one project with `--project` | `cmru <verb> [all|<registered-project>]`; omitted means the current project in project context and all at a CMRU root |
| `build`, `publish` | all projects from orchestration; one with `--project` | the same positional selector and context defaults |
| `run` | explicit project/step selection | same context defaults, with explicit selectors retained |
| `resolve` | one required `--project` or `--prefix` | `resolve [all|<project>[,<project>...]]`; omitted means current project or all |
| `get`/`get-py` | required `--config` and `--project` | current project, an explicit registered project, or `all`; multi-project output is headed or written to a per-project directory |
| `changelog` | required project selector | current project default; a registered name or `all` uses the same positional selector and emits per-project result blocks |
| `standards` | every project in the selected config by default | current project in project context; all in CMRU-root context; positional selector is shared with other verbs |
| `tool-deps` | every project in the selected config by default; cross-project checks require an orchestration file | current project default, with the nearest central registry available to resolve providers |
| `cleanup` | estate config by default; project selector is optional | current project default when launched in a project; estate-wide only from CMRU-root context or explicit `all` |
| `dependencies` | orchestration config required; graph is estate-wide | nearest central config is selected automatically; graph remains estate-wide |
| `run-step` | explicit project config is required | current project config is the default; one step remains required |
| `worktrees` | Git-only, no CMRU config | unchanged |
| `tester-gate`, `handler` | direct execution surfaces, no CMRU project registry | unchanged |
| `init` | generates project and/or orchestration documents from a small prompt set | an adoption wizard asks for one or more existing folder paths, project names, project type, CMRU-root/layout, repository facts, and release decisions; it previews and validates the generated contracts before writing |

## Target contract

### 1. Resolve an invocation context once

Add one loader-owned resolver that returns:

```text
InvocationContext(
    config_path: Path,
    config_kind: "project" | "orchestration",
    project_name: str | None,
    scope: "project" | "estate",
)
```

Resolution order:

1. An explicit `--config` is authoritative and is never replaced by search.
2. Otherwise walk the filesystem ancestors from the current directory to the
   filesystem root. The nearest `cmru.orchestration.toml` is the selected
   central file; Git discovery does not bound this search.
3. The selected orchestration file establishes the **CMRU root** at its parent
   directory. That root may be inside a repository, at a repository root,
   above several repositories, or in a user-owned directory outside the
   repositories it serves.
4. If the selected CMRU root contains a registered project whose config path
   contains the current directory, use the orchestration file in **project
   scope** with that project as the implicit target. If a nearer `cmru.toml`
   exists, it must be the registered config for that project; otherwise refuse
   rather than silently selecting a different project.
5. If no registered project contains the current directory and no local
   `cmru.toml` identifies an unregistered project below that CMRU root, use the
   selected orchestration file in **CMRU-root scope**. This is the context in
   which an omitted target means `all` for verbs that support estate-wide
   operation.
6. If no central file exists, use the nearest directly discovered `cmru.toml`
   in **project scope**. A standalone project config must still contain enough
   repository facts to operate; otherwise fail with the exact missing source
   and the command that supplies it.
7. If a nearest central file exists and the current directory is inside a
   `cmru.toml` project below that CMRU root, but that project is not registered,
   refuse and identify both the project config and central file. Do not search
   past that CMRU root for another credential or registry scope.

The CMRU-root boundary is therefore defined by the nearest orchestration file,
not by Git. A user can place one orchestration file above several repositories
and register those repositories by relative config paths. A nested orchestration
file starts a new CMRU root for its subtree. The loader must resolve and
validate registered paths against the selected CMRU root, including symlink
handling, so a path cannot escape the root accidentally. The project config may
be in the CMRU-root directory itself: containment is inclusive, so a project
whose config is `cmru.toml` beside `cmru.orchestration.toml` is valid and that
directory is both the project root and the CMRU root. Deliberate symlink escapes
remain refused.

All verbs must consume this one result. They must not independently search for
configuration or independently infer the current project.

### 2. Centralize repository facts

Move the authoritative repository identity and target declarations to the
root orchestration document:

```toml
[github]
owner = "volkb79-2"
repo = "vbpub"
owner_type = "user"

[targets]
host = "github"
registry = ["ghcr.io"]
```

Project `cmru.toml` files in an orchestration tree omit these tables. The
orchestration loader requires the central tables once and passes the resolved
values to every project. A project-level duplicate is rejected rather than
silently ignored.

For a directly invoked standalone project config, the local tables remain the
authoritative source when no central orchestration file is selected. If they
are absent, fail clearly and point at the required central document or explicit
configuration. This supports a repository that deliberately does not ship a
central file without duplicating facts when it is part of a CMRU-root tree.

The CMRU-root `cmru.secret.toml` remains the credential source. Its token is
loaded because the selected orchestration file establishes the CMRU root; a
project-local secret remains an explicit overlay. This also covers a user-owned
CMRU root above several repositories: the sibling secret belongs to that
selected central file, not to whichever Git repository happens to contain the
current directory. The orchestration TOML never contains secret material.

### 3. Normalize project selection

Every verb that selects registered projects uses one shared positional grammar:

```text
cmru <verb>                         # context default
cmru <verb> all                     # every registered project
cmru <verb> <project-name>          # one registered project
cmru <verb> ciu,assay,nyxloom       # several named projects
```

`all` is a reserved project identifier. The schema loader rejects a project
whose registered name is `all`, and the selector parser treats it as the
estate-wide scope. There is no deprecated `--project` alias: a target-selection
`--project` is removed from the public grammar and is rejected with the normal
version-headed usage error. All help, documentation, shell examples, and
generated command lines use the positional grammar.

The selector accepts a comma-separated list for every project-aware verb.
Whitespace around commas is trimmed; empty elements and duplicate names are
errors. `all` is exclusive, so `all,ciu` is rejected rather than silently
deduplicated. The parser and dispatcher share one target object, so target
selection is resolved once and passed to each verb. Destructive verbs must
print the resolved scope before acting; an implicit project target must never
silently expand to the estate. The canonical documentation places the target
directly after the verb, while the parser may accept it in the ordinary
argparse positional location relative to options.

When execution order matters, especially for release/build dependency safety,
the selected set is executed in the orchestration's declared project order.
Output uses the same stable order, so `ciu,assay,nyxloom` selects exactly those
projects without turning command-line order into an undeclared dependency rule.

The wizard has no project-target `--project` option. The `--project` options
belonging to delegated/private interfaces are classified explicitly rather
than accidentally rewritten as CMRU registry selectors; any such option that
names a registered project is migrated to the shared positional target.

The target matrix is explicit. Multi-project verbs such as `status`, `release`,
`build`, `publish`, `run`, `resolve`, `standards`, `tool-deps`, and `cleanup`
accept `all`, one registered name, a comma-separated registered-name list, or
the context default. `changelog` and `get-py` accept the same forms, with the
distinct per-project output contracts described below. Inherently estate-wide
graph commands such as `dependencies` do not gain a meaningless project
selector.
The final matrix is part of the public specification and is tested.

`resolve --format json` returns one object for one project and a stable
project-keyed object for multiple targets or `all`. `env` emits one block per
project with stable ordering. `url` returns the single raw URL for one target;
for multiple targets it emits headed project/URL lines rather than an
ambiguous concatenation. “No release” remains distinct from network,
authentication, and configuration failure.

The public `--prefix` option is removed. `cmru resolve ciu` resolves the
registered project's configured prefix, credentials, and repository identity;
there is no second spelling that accepts a raw tag prefix. An unregistered or
legacy release series must first be represented by a registered project config
or an explicit standalone config, so resolution always has one auditable
project identity.

`changelog all` produces the requested headed result blocks:

```text
===== Changelog Project: CIU =====================
... project-specific result ...
===== Changelog Project: ASSAY ===================
... project-specific result ...
```

The output headings solve presentation, while the input still needs to identify
which already-published tag belongs to each project. `--backfill-tag` therefore
becomes repeatable for `all`; each supplied tag is matched to exactly one
selected project's configured tag prefix, and unmatched or ambiguous tags are
errors. For example:

```text
cmru changelog all \
  --backfill-tag ciu-v1.2.0 \
  --backfill-tag assay-v0.8.1
```

`get-py all` likewise emits a headed section per project on stdout. When
writing files, its all-project form takes an output directory and writes one
named script per project; the single-project `--output FILE` contract remains
unchanged.

### 4. Make `init` an adoption wizard

The current `init` implementation is already interactive, validates generated
files with the real loaders, and refuses to overwrite existing targets. It is
not yet an adoption wizard: it assumes the current directory, has one wheel
template, and asks only for a small set of repository facts.

The new default `cmru init` flow should ask, in order:

1. the existing folder path or CMRU-root path to adopt, defaulting to the
   current directory and accepting paths outside the current directory;
2. whether this is one project, a CMRU root for several projects, or a project
   that starts its own root in the same directory;
3. one or more project folder paths and assigned registered names;
4. detected project type, with explicit confirmation or a manual template
   choice;
5. repository owner/name, tag prefix, released artifact kinds, versioning,
   gate/build/publish commands, and local retention choices;
6. a complete file-and-change preview before validation and writing.

Initial templates should cover the shipped artifact vocabulary (`wheel`,
`tarball`, `bundle`, and `oci-image`) plus an explicit generic-command path.
Detection may suggest a template from files such as `pyproject.toml`, a bake
file, or a declared archive, but it must ask when evidence is ambiguous and
must never invent a release command. A project can be adopted in the same
directory as its `cmru.orchestration.toml`; the wizard writes a relative
`cmru.toml` entry for that inclusive-root case. Existing files remain
protected, and the generated contracts are loaded and standards-checked in a
throwaway tree before any real write.

### 5. Make the version headline universal for CLI usage and argument errors

Every CMRU-owned `usage()` result and user-facing argument/configuration error
starts with the current first line:

```text
CMRU <current-version> — Configurable Multi Release Utility
```

The version is generated from the shipped version source, so the literal value
tracks the release. The existing top-level `usage()` already has this headline;
the delegated parsers and ad-hoc `parser.error()` paths do not consistently
inherit it. Introduce one shared parser/error-rendering path and route every
public CMRU verb through it. Tests will assert the first line for help, missing
arguments, unknown selectors, invalid combinations, missing config, and the
rejected target-selection `--project` flag. Runtime failures that are not
usage/configuration diagnostics remain ordinary command errors unless the
interview expands this requirement.

## Implementation slices

1. **Config model and loaders** — update `cmru/src/cmru/config.py` and
   `config_names.py`; add central repository facts, optional standalone facts,
   nearest-CMRU-root resolution to the filesystem root, registration checks,
   root-contained path/symlink validation, secret-root provenance, and an
   explicit `InvocationContext` owner.
2. **CLI plumbing** — update `cmru/src/cmru/cli.py` so every config-bearing
   verb resolves context once, parses the shared comma-separated positional
   target, rejects `--prefix` and target-selection `--project`, applies the
   correct default target, and emits the version headline on every usage/error
   path. Keep project/estate selection out of individual ad-hoc loaders.
3. **Consumer verbs** — update `resolve.py`, `getpy.py`, `runner.py`,
   `changelog.py`, `standards.py`, `tool_deps.py`, and cleanup/release/build
   dispatch paths. Remove public target-selection `--project`, preserve
   creation/delegated/private uses only where the final classification allows
   them, and route all public parsers through the shared headline renderer.
   Audit direct `tester-gate`, `handler`, `worktrees`, and `version` surfaces
   as well: retain their non-registry options where they are genuinely
   tool-specific, but give every CMRU-owned usage/error path the same headline.
4. **Scaffolding and estate migration** — update `scaffold.py`, templates,
   `cmru.orchestration.sample.toml`, the repository-root orchestration file,
   and every registered project `cmru.toml`. Remove duplicated `[github]` and
   `[targets]` from the vbpub project documents, and reject `all` as a
   registered project name. Replace `init --project` with the adoption wizard,
   add type-specific templates, and cover same-directory project/CMRU-root
   adoption.
5. **Native release logging and wrapper retirement** — move the wrapper's
   remaining behavior into `cmru release`: automatic CMRU-root config
   discovery, `PYTHONUNBUFFERED`, a default aggregate `cmru.release.log`,
   append semantics, and live teeing. Once direct `cmru release all` has parity,
   remove `cmru.release.sh` and update all references. Artifact/log retention
   remains the CMRU release default; an explicit discard option is independent
   of wrapper removal and must be decided as part of the public CLI review.
6. **Contract documentation** — update `cmru/README.md`,
   `cmru/docs/SPEC.md`, `cmru/docs/CONSUMERS.md`, release-transaction guidance,
   changelog/help text, and generated examples. State the project-context,
   estate-context, explicit-config, secret-root, and selector rules together.
   Make `usage()` lead with copyable default examples before the exhaustive
   option matrix.

## Proof required before implementation is considered complete

- Loader tests cover project-at-root, project-in-subdirectory, an orchestration
  file above several Git repositories, nested central configs, filesystem-root
  termination, unregistered project refusal, explicit config precedence, and
  non-Git operation. They prove that Git-root boundaries do not affect CMRU
  discovery and that the nearest orchestration file establishes the CMRU root.
- Secret tests prove that project-context invocation reads the nearest central
  secret, project overlays win, a different parent repository is never read,
  and a missing credential is reported as missing rather than “no release.”
- Schema tests prove central `[github]`/`[targets]` are required, project
  duplicates are rejected in orchestration mode, standalone local facts still
  work, and missing facts fail loudly.
- Selection tests exercise every config-bearing verb in project and CMRU-root
  contexts, including the positional omitted/`all`/registered-name cases. At
  least one test must prove a project-context destructive command does not
  expand to all projects, and another must prove `all` cannot be registered as
  a project name.
- Selection tests also cover comma-separated names, whitespace trimming,
  duplicate/empty-name refusal, and `all` mixed with another name.
- Resolve and verb parser tests cover omitted selector, `all`, registered name,
  comma-separated names, rejected target-selection `--project`, rejected
  `--prefix`, invalid combinations, JSON/env/url output, and distinct
  no-release/network/auth/configuration outcomes.
- Multi-project output tests prove project headings/keys for changelog,
  get-py, resolve URL output, and every other verb whose output would otherwise
  be ambiguous.
- A parser matrix proves that every public `usage()` and argument/configuration
  error begins with the current CMRU version headline, including delegated
  `resolve`, `get-py`, `standards`, and `tool-deps` paths.
- `cmru init` tests exercise an existing folder path, same-directory
  project/CMRU-root adoption, multiple project paths, confirmed type detection,
  ambiguous/manual type selection, preview refusal, existing-file refusal, and
  real-loader validation before writing.
- Release parity tests prove that direct `cmru release all` supplies the
  wrapper's aggregate log, append, live-output, config-discovery, and
  unbuffered-child behavior before the wrapper is removed.
- Scaffolding tests load every generated example with the shipped loader and
  verify the central repository facts are present exactly once.
- Run the CMRU test suite, `cmru standards` against the estate config, and the
  CMRU project gate. No release or publish command is part of this work.

## Out of scope

- Publishing, tagging, changing release transaction ordering, or changing
  GitHub/GHCR APIs.
- Moving project-owned build, test, or publish commands into the orchestration
  document.
- Making secrets part of committed TOML.
- Changing `run-gate` behavior; that migration is a separate package.

## Usage review and recommended examples

The current `usage()` is version-headed and comprehensive, but it teaches the
old `--project` grammar and puts the most common commands behind an option
matrix. Rewrite the opening examples around contextual defaults and explicit
`all`:

```text
GETTING STARTED
  cmru init                                  adopt a folder with the wizard
  cmru status                                preview the current project
  cmru status all                            preview every registered project

RELEASE
  cmru release <project> --dry-run           preview one isolated release
  cmru build ciu,assay,nyxloom                build several selected projects
  cmru release all --dry-run                 preview the estate release
  cmru release all                           gate, integrate, tag, build, publish
  cmru cleanup <project> --dry-run           inspect project cleanup

CONSUME RELEASES
  cmru resolve                               current project, JSON by default
  cmru resolve all --format json             resolve every project by name
  cmru resolve ciu,assay --format json       resolve several selected projects
  cmru get-py ciu --output ciu-get.py        write one installer
  cmru get-py all --output-dir installers    write one installer per project
```

Follow these with the complete option forms and the context rules. The
headline must remain the first line, and every example must parse with the
shipped loader/parser tests.

## Decisions recorded

The wizard's first release exposes all four artifact types plus generic
commands. Changelog backfill uses repeated raw tags matched by configured
project prefix, and the output is headed per project. Explicit
`--discard-logs-on-release` and `--discard-artifacts-on-release` remain as
opt-outs; retention stays the default after wrapper removal.
