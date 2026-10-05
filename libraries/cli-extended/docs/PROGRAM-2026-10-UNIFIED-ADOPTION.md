# cli-extended unified-adoption program (2026-10)

The goal is for every vbpub Python tool with a command line to use
`cli-extended`. The library absorbs the boilerplate that each tool currently
hand-rolls. It also owns the whole CLI-contract workflow:

1. registration,
2. generated surface,
3. LLM semantic review,
4. final spec,
5. actionable findings,
6. packaged agent skills.

This file records the operator decisions from the 2026-10-04/05 interview, the
library wave packages (W1–W8), the first consumer adoption (W9) and the
planned order for the remaining tools (W10). It is the source document that
implementer briefs cite. Backlog entries `CLI-EXT-05`…`CLI-EXT-15` in
[`BACKLOG.md`](../BACKLOG.md) track each item.

## Operator decisions

| ID | Decision |
|---|---|
| CX-D1 | **Distribution is a real wheel dependency (option C) for every tool, run-gate included.** Each tool declares `cli-extended>=<floor>` in `[project].dependencies` and stops vendoring through `package-dir`, `PYTHONPATH` or `sys.path`. The assay wheel declares the same dependency, and the assay zipapp build bundles the pure-stdlib `cli_extended` package, so "copy the `.pyz` into a pip-less image" still works. assay's A-005 is reworded to "no third-party runtime dependencies". |
| CX-D2 | **Resolution is from GitHub Releases with `--no-index`, never PyPI.** `cli-extended` is released by cmru like every tool (GitHub Release plus a `cli-extended-latest/latest.json` pointer). Every installer (dstdns env-setup, cmru `[[installer.wheels]]`, mdt, devcontainer deploy) installs it first with sha256 verification, and installs tool wheels with `--no-index --find-links <cache>`. The bare name is unclaimed on PyPI (verified 404 on 2026-10-05), so a default `pip install tool.whl` would be a dependency-confusion hole. |
| CX-D3 | **Scripts.** The Netcup scripts import the installed `cli_extended`. Their gate lanes keep `PYTHONPATH` pointed at the worktree source, so the revision under test is the one tested. debian-install-v2's remote bootstrap downloads the released `.whl`, verifies it by sha256 from the release manifest, and imports it with zipimport (`sys.path` entry), so the target needs no pip. It no longer downloads a source subtree. |
| CX-D4 | **Versioning.** The library stays on `0.x` (first release `0.2.0`) until every planned adoption is done, then cuts `1.0.0`. A separate integer **`contract_version`** (starting at 1) versions the library-owned common controls; see CX-D5. |
| CX-D5 | **Surface signatures cover only consumer-declared grammar.** Library-owned common controls appear in the manifest only as a per-route list of enabled control names, plus one top-level `library_contract` version. Upgrading the library without a contract bump leaves every consumer manifest byte-identical. A contract bump produces exactly one `check` finding that tells the consumer to re-sync. |
| CX-D6 | **The LLM semantic check is run by the harness through a skill.** `cli-extended surface pack` emits a review bundle. The packaged `cli-extended-review` skill makes the agent judge it, then write catalog decisions and a findings file. The library makes no model API calls, needs no keys, and stays stdlib-only. |
| CX-D7 | **Adoption checklist** has three parts: `docs/ADOPTION-CHECKLIST.md`, a mechanical `cli-extended audit` for what is cheaply decidable, and a packaged `cli-extended-adoption` skill for the final judgement on items marked `manual`. |
| CX-D8 | **cli-extended gets its own CLI** (`cli-extended` console script), built on its own `CliRegistry`. `python -m cli_extended.surface_cli` remains as a deprecated alias. |
| CX-D9 | **Skills are installed per tool**: `<tool> skills install\|list\|check\|uninstall`, provided by one `register_skills_verbs(...)` call. There is no global cross-tool sync. The install stamp is written into the SKILL.md frontmatter `metadata` and a visible banner line; an integrity sidecar records file hashes. |
| CX-D10 | **Extra shared features in this wave:** dry-run integration, a shared `doctor` verb, declarative option constraints (CLI-EXT-02 reopened) and a selector-list value type. These are in addition to the version resolver, exception boundary, test helpers and project config. |
| CX-D11 | **Adoption order:** ① Netcup and debian-install-v2 → ② cmru → ③ nyxloom → ④ cgprofile → ⑤ pwmcp → ⑥ assay → ⑦ run-gate → ⑧ ciu. **This session executes W1–W9 (library wave plus ①); ②–⑧ are planned as backlog entries in each tool's own backlog.** |
| CX-D12 | **run-gate fully adopts** (its whole grammar is re-registered) like every other CLI, independent of the v8 merge into ciu. |

## Process

- **Integration branch:** `cli-extended-unified` (worktree `.worktrees/cli-extended-unified`). Each package gets its own worktree and branch off the integration branch, and merges back `--no-ff` after review.
- **Roles:** the controller is Opus. Implementers and adversarial reviewers are Sonnet, in fresh sessions, never forks (memory `implementers-sonnet-only`). Each package gets at most 3 review rounds.
- **Gates:**
  - Each package passes the registered `r0-r1` lane: 100% statement and branch coverage, run through `run-gate.py` in tester-unified. Implementers iterate with focused `nice -n 19 ionice -c3` pytest runs only.
  - The full wave gate (`r0-r1` → `r2` mutation at 100% killed → `r3` canary) runs on the integrated branch before the merge to `main`.
  - Only one gate container runs at a time, because the host is shared with a production game server.
- **Docs are part of each package** (AGENTS.md "User-facing docs" rule). Every package updates `README.md` (what), `docs/DESIGN-GUIDE.md` (why), `docs/CONSUMERS.md` (how, with pasteable examples) and the normative `SPEC.md` in the same change. `tests/test_docs.py` keeps examples parseable by the shipped loader.
- **Shipped** means merged to `main` and pushed, released with `cmru release --project cli-extended`, and installed into `/home/vscode/.venv` with the installed version verified.

## Library wave

Order and dependencies:

```
W1 runtime boundary ─┬─> W2 constraints+selector ─┐
                     ├─> W4 skills ──> W5 doctor ──┤
W3a contract version ┴─> W3b config+CLI+review ────┼─> W6 test helpers ─> W7 audit ─> W8 release
                                                   ┘
```

W1 and W3a can run in parallel because their files are disjoint (`identity.py`/`parser.py` versus `surface.py`/`review.py`). Everything that touches `parser.py` runs serially.

### W1 — identity and runtime boundary (`identity.py`, `parser.py`)

1. **Version resolver.** `CliIdentity.resolve(*, name, long_name, command=None, distribution=None, version_file=None)`.
   - At least one source is required; zero sources raises `ValueError`.
   - `version_file` must be an absolute path; a relative one raises `ValueError`. Its stripped content must match `^\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.+-]+)?$`.
   - The installed distribution metadata is read first, then the version file.
   - If both resolve and disagree, raise `VersionLookupError` naming both values.
   - If neither resolves, raise `VersionLookupError` naming every source tried.
   - There is never a literal fallback.
   - `from_distribution` stays as is.
2. **Exception boundary.**
   - `CliRegistry(..., expected_exceptions=(), unexpected_exceptions="raise")` is forwarded by `RegisteredCli.run()`. A run-time `expected_exceptions=` kwarg is unioned with the registry value.
   - `unexpected_exceptions="report"` turns any uncaught `Exception` from parsing-after-dispatch or a handler into `[ERROR] unexpected <Type>: <message>`, with hint `rerun with --traceback to see the stack`, and exit 1.
   - That mode also adds a library-owned common option `--traceback` (group DEBUGGING). When given, it re-raises the original exception unchanged.
   - `"raise"` keeps today's behavior and does not add `--traceback`.
   - Any other value raises `ValueError` at registry construction.
   - `KeyboardInterrupt`, `PromptCancelled`, `SystemExit` and `CliFailure` keep their existing handling.
3. **Dry-run.**
   - `VerbSpec.dry_run: bool = False` is valid only with `mutating=True`; otherwise it raises `ValueError`. It adds a library-owned `--dry-run` (group CONFIRMATION) to that verb and exposes `runtime.dry_run`.
   - `--dry-run` on a verb without it is refused with exit 2 and verb help, exactly like an unsupported `--json`. This holds before or after the verb token.
   - While `runtime.dry_run` is true, `runtime.confirm()` never prompts and never honors `--yes`. It emits `Dry run: no changes made.` (info, forced) and returns `False`. A handler that already gates its mutation on `confirm()` therefore becomes dry-run-safe without new branches.
   - Help, Markdown, catalog behavior labels (`dry-run`) and `assert_cli_contract` all render it.

### W2 — declarative constraints and selector type (CLI-EXT-02, CLI-EXT-12)

1. **New module `constraints.py`.** It exports `Requires(option, any_of, reason)`, `Conflicts(options, reason)` and `RequiresChoice(option, target, values, reason)`. A verb lists them in `VerbSpec.constraints: tuple[...] = ()`.
   - Every referenced flag must be accepted by that verb: local, global or enabled common option. Otherwise `ValueError` at `build()`.
   - "Present" means the parsed value differs from the action's default. Registration therefore refuses (`ValueError`) any referenced action whose default is not `None`, `False` or an empty sequence. This fails loudly instead of misdetecting presence.
   - `Conflicts` needs ≥ 2 options. `Requires.any_of` needs ≥ 1 entry. `RequiresChoice.values` must be a non-empty subset of the target's `choices`.
   - Enforcement runs after parsing, before the handler. A violation is a `CliFailure(exit_code=2, show_help=True)` whose message names the option, the rule and the consumer's `reason`.
   - Verb help gains a `CONSTRAINTS` section, and Markdown reference renders the same lines.
   - The surface exports each constraint (consumer-declared, signed) and generates one refusal candidate per constraint (`constraint-requires`, `constraint-conflict`, `constraint-choice`). The review checker treats those invocations as syntactically valid.
2. **New module `values.py`.** It exports `SelectorList(choices=None, *, all_token="all", separator=",")`, an argparse `type` callable.
   - `"all"` alone returns the full `choices` tuple, or the `SelectorList.ALL` sentinel when `choices is None`.
   - Mixing `all` with names, an empty item, a duplicate, or an unknown name (when `choices` is set) raises `argparse.ArgumentTypeError` with the valid names listed.
   - Semantics are matched against `cmru/src/cmru/cli_support.py:parse_target_names`; any intentional difference is documented.
   - The surface recognizes the exact class, records `choices`, `all_token` and `separator`, does not mark it opaque, and the review checker models it.

### W3a — library contract version (`surface.py`, `review.py`, new `contract.py`)

- **`contract.py`** is the single source of `CONTRACT_VERSION = 1` and of the library-owned common-control set: every flag produced by `_common_option_specs` plus W1's `--traceback`/`--dry-run`. Nothing else may restate that set.
- **Manifest:**
  - It gains a top-level `"library_contract": {"name": "cli-extended", "version": CONTRACT_VERSION}`.
  - Each route gains a sorted `"common_controls"` list of the enabled library-owned flags.
  - Route `actions` and the Markdown option rows no longer contain the shapes of library-owned controls. The Markdown region renders one line per route listing the enabled controls, linking to the library contract in SPEC.md.
- **Candidates:**
  - The consumer-review candidates for `--json`, `--yes` and `--debug-raw` stay.
  - Their signature context contains the control name and route only, not its help text or action shape.
- **Check:**
  - A manifest whose `library_contract.version` differs from the running library yields exactly one finding: `cli-extended contract changed v<old> → v<new>; read CHANGES.md contract notes, then run 'cli-extended surface sync'`. Check fails without listing per-case signature noise.
- `SURFACE_SCHEMA_VERSION` becomes 7 once for the whole wave; W2 adds its fields inside 7.
- **Oracles:**
  - Monkeypatching the help text or `metavar` of any library-owned control leaves `render_cli_surface_json` and the Markdown region byte-identical.
  - Changing a consumer `OptionSpec` help still changes signatures.
  - Bumping `CONTRACT_VERSION` in a test yields the single finding.

### W3b — project config, own CLI, and the review workflow

1. **`config.py`.** It reads `[tool.cli-extended]` from `pyproject.toml`, or the same schema at top level in `cli-extended.toml` for projects without a pyproject (Netcup):

   ```toml
   [tool.cli-extended]
   schema_version = 1
   [[tool.cli-extended.clis]]
   id = "monitor-task"                 # must equal identity.command_name
   factory = "monitor-task.py:build_cli"
   review = "cli-review.toml"
   manifest = "cli-surface.json"
   spec = "CLI-SPEC.md"
   findings = "cli-review-findings.toml"   # optional
   ```

   - Discovery is `--config PATH`, or a walk upward from the cwd to the first directory holding either file. Both files in one directory is an error. No config found is an error naming the searched directories.
   - Paths are relative to the config file. Unknown keys and a wrong `schema_version` are errors.
   - `--cli ID` is required when more than one CLI is configured.
2. **`cli.py`, the `cli-extended` console script.** It is built with `CliRegistry`, using `CliIdentity.resolve(distribution="cli-extended")` and `unexpected_exceptions="report"`.
   - `surface sync|check|template|pack|report` is a delegate group.
   - `audit` comes from W7, and `skills install|list|check|uninstall` from W4 via `register_skills_verbs(package="cli_extended")`.
   - `python -m cli_extended.surface_cli` keeps its flags, prints a one-line deprecation warning to stderr, and calls the same functions.
3. **`surface pack`.**
   - It writes a Markdown review bundle to stdout or `--output FILE`.
   - The bundle contains:
     - the packaged rubric (`cli_extended/review_rubric.md`);
     - the entrypoint identity and contract version;
     - each route's rendered plain help;
     - every pending, changed and stale case with its shape and current catalog row;
     - open findings.
   - The rubric covers:
     - naming and flag consistency;
     - help clarity and examples;
     - mutating verbs without confirmation or dry-run;
     - read verbs without `--json`;
     - exit-code meaning;
     - undeclared option conflicts or dependencies (constraint candidates);
     - hidden-option justification;
     - positional-versus-option choice;
     - `configure` callbacks that could be declarative.
4. **Findings file** (`schema_version = 1`, `cli_id`, `[[findings]]` with `id`, `status`, `severity`, `category`, `route` (optional), `summary`, `remedy` and `rationale`):
   - `status` is one of `open`, `fixed`, `wontfix`.
   - `severity` is one of `blocker`, `major`, `minor`, `note`.
   - `category` is one of `grammar`, `help`, `semantics`, `consistency`, `adoption`.
   - `rationale` is required for `wontfix`.
   - A `route` that no longer exists makes the finding stale (a check finding).
   - `check` fails on any open `blocker` or `major`. `sync` renders open findings into the spec region under "Open review findings".
   - `surface report` prints the actionable list: open findings by severity, then pending/changed/stale cases, then incomplete syntax.
5. **Packaged skill `cli_extended/skills/cli-extended-review/SKILL.md`.** It is the agent procedure: sync → pack → judge with the rubric → edit catalog rows and the findings file by hand (the library never rewrites them) → sync → check → report.

### W4 — packaged agent skills (CLI-EXT-05, new `skills.py`)

- **Layout:**
  - The consumer package ships `<package>/skills/<skill-name>/SKILL.md` (plus any files) as package data.
  - `register_skills_verbs(registry, *, package, resource_dir="skills")` adds a `skills` delegate group with `install`, `list`, `check` and `uninstall`.
  - The tool name is `identity.command_name` and the version is `identity.version`.
  - `importlib.resources` locates the files, so wheels, editable installs and zipapps all work.
- **Source schema.** It follows the Agent Skills spec subset that both harnesses read:
  - The frontmatter is the first `---`…`---` block. It allows only single-line `key: value` scalars plus an optional `metadata:` block of two-space-indented `key: value` lines; anything else (folded or literal YAML) is a validation error naming the line.
  - `name` is required, at most 64 characters, matches `[a-z0-9]+(-[a-z0-9]+)*`, and equals the directory name.
  - `description` is required, 1–1024 characters.
  - Source must not contain `metadata` keys starting with `cli-extended-`.
- **Stamp.** The installed SKILL.md gains:
  - `metadata` keys `cli-extended-tool`, `cli-extended-version` and `cli-extended-source-hash` (sha256 over sorted relative-path, NUL, length, NUL, bytes records of the source tree);
  - one banner line directly after the frontmatter: `> Installed by <tool> <version> via cli-extended. If this disagrees with \`<tool> --help\`, run \`<tool> skills check\`.`

  The sidecar `.cli-extended-stamp.json` records tool, version, source hash and a sha256 per installed file.
- **Targets:**
  - `--harness claude|agents|all` defaults to `all`, a policy choice per D-647 #6.
  - `claude` installs into `$CLAUDE_CONFIG_DIR/skills` when that variable is set, else `~/.claude/skills`.
  - `agents` installs into `~/.agents/skills`.
  - `--dest DIR` (mutually exclusive with `--harness`) installs into exactly that directory.
- **States:**

  | State | Meaning |
  |---|---|
  | `absent` | Not installed. |
  | `current` | Installed, unmodified, matching version and source hash. |
  | `stale` | Unmodified, but the version or source hash differs. |
  | `modified` | Installed file hashes differ from the sidecar, or files were added or removed. |
  | `foreign` | Stamped by another tool. |
  | `unmanaged` | Directory exists without a stamp. |
  | `orphaned` | Stamped by this tool but no longer packaged. |

- **Verbs:**
  - `install` is mutating and `dry_run=True`, and uses `confirmation_required=False` except as below.
    - Absent, stale and current skills are written atomically (temp sibling, then rename). Current skills are rewritten as a no-op, and byte equality is verified.
    - Orphans are removed.
    - `modified` is refused unless `--overwrite-modified` is given (as built; the plan said `--yes`, see BACKLOG CLI-EXT-05).
    - `foreign` and `unmanaged` are always refused and never overwritten.
  - `uninstall` removes only skills stamped by this tool; `modified` needs `--overwrite-modified`.
  - `check` exits 1 when any skill is not `current` (including orphans) and supports `--json`.
  - `list` prints skill × harness → state and supports `--json`.
- **Backlog oracles (all mandatory):**
  - Installing twice is idempotent.
  - An upgraded version makes the skill stale, and installing refreshes it.
  - A local edit makes it modified, and it is not overwritten without `--overwrite-modified`.
  - A foreign skill is refused.
  - A controlled wrong implementation that copies without stamping fails the foreign-refusal oracle.

### W5 — shared doctor (new `doctor.py`)

- **API:**
  - `DoctorCheck(name, description, run)` where `run(runtime, args) -> CheckResult` (as built; the plan said `run(runtime)`).
  - `CheckResult(status, summary, remedy=None, details={})`, with `status` one of `ok`, `warn`, `fail`, `skip`.
  - `register_doctor(registry, checks, *, description=...)` registers a read-only `doctor` verb with `--check NAME` (repeatable; an unknown name is a usage error) and `--json`.
- **Behavior:**
  - A check that raises becomes `fail` with summary `check crashed: <Type>: <message>`, so an exception is never swallowed as ok.
  - Exit 0 when nothing failed; exit 1 when any check failed.
  - When the registry also has `register_skills_verbs` (either registration order), the doctor automatically includes a `skills` check equivalent to `skills check`.

### W6 — consumer test helpers (`testing.py`, new `pytest_plugin.py`)

- **`invoke_script(script, argv, *, home, python=sys.executable, scrub_prefixes=(), env=None, cwd=None, timeout=60)`.**
  - `home` is **required**. The child gets `HOME=home`, all four `XDG_*_HOME` under it, `NO_COLOR=1`, no `FORCE_COLOR` and no `CLAUDE_CONFIG_DIR`.
  - Environment keys matching `scrub_prefixes` are removed.
  - `PYTHONPATH` is prefixed with the directory of the *imported* `cli_extended` package, so the child runs the tested library revision.
  - `invoke_module(module, argv, ...)` is the `-m` twin.
  - `make_invoker(...)` returns the callable `assert_cli_contract` expects.
  - (The 2026-10-04 leaked run-gate test shim in `~/.local/bin` shows why `home` is mandatory.)
- **`cli_extended.pytest_plugin`.**
  - It is opt-in: one `pytest_plugins = ["cli_extended.pytest_plugin"]` line in the root conftest, never auto-loaded through an entry point.
  - It registers the `cli_case` marker. At collection finish it runs `assert_cli_case_tests` for every configured CLI that has a review catalog (via W3b config).
  - It is strict by default; `--cli-case-partial` checks only the collected items, for focused local runs.

### W7 — adoption checklist and audit

- **`docs/ADOPTION-CHECKLIST.md`** lists every library feature, with a stable id (`AC-01`…), what correct use looks like and how it is verified (`audit` or `manual`).
- **`cli-extended audit [--cli ID] [--json]`** inspects the built `RegisteredCli`, the project config and the source tree. It reports each item as `pass`, `warn`, `fail` or `manual`, with evidence, a remedy and the checklist anchor, and exits 1 on any `fail`. Mechanical items:
  - **Surface:** the surface `check` passes, and the surface is syntax-complete.
  - **Grammar:**
    - a declared `synopsis` equal to the derived one is redundant (warn), and any override is manual;
    - consumer-declared options that shadow library controls (`--dry-run`, `--yes`, `--json`, `--traceback`, `--no-color`, `--quiet`) fail;
    - mutating verbs with neither confirmation nor dry-run are manual;
    - `configure` callbacks are manual;
    - hidden options are manual.
  - **Runtime policy:** `unexpected_exceptions="raise"` is a warn.
  - **Dependency:** the pyproject declares `cli-extended>=`, and no `package-dir`/`sys.path`/`PYTHONPATH` vendoring of `cli_extended` appears in the project sources (a heuristic text scan, labelled as such).
  - **Skills:**
    - skills are packaged and the `skills` verbs are registered;
    - every packaged SKILL.md validates;
    - a `.claude/skills` source tree that duplicates packaged skills fails.
  - **Tests:** the pytest plugin is enabled when a review catalog exists (heuristic).
  - **Doctor:** a `doctor` verb exists (manual whether it is needed).
- **Packaged skill `cli-extended-adoption`** walks an agent through `audit`, then judges every `manual` item against the checklist and records the outcome in the findings file (`category = "adoption"`).

### W8 — packaging, cmru onboarding, release

- **pyproject:**
  - version `0.2.0`;
  - `[project.scripts] cli-extended = "cli_extended.cli:main"`;
  - package data for `skills/**` and `review_rubric.md`;
  - `CHANGES.md` created, including a "Contract" section (contract v1 defined).
- **cmru onboarding:** a `libraries/cli-extended/cmru.toml` modeled on run-gate-project's pure-wheel contract. It is registered in the root `cmru.orchestration.toml` (`project_order`, `default_projects`, `[orchestration.project.cli-extended]`) and publishes `cli-extended-latest/latest.json`.
- **Release:** `cmru release --project cli-extended --set-version 0.2.0`.
- **Deploy:**
  - Install into `/home/vscode/.venv` and verify `cli-extended version`.
  - Then verify which `cli_extended` the venv imports. Today it resolves through cmru's editable finder to the main checkout source (`libraries/cli-extended/src`). Record the result and the precedence, and keep the conflict tracked until cmru adoption (②) removes vendoring.

## W9 — first adoption: Netcup and debian-install-v2

- **Netcup:**
  - Add a `cli-extended.toml` with all three CLIs.
  - Replace the triple VERSION-regex identity blocks with `CliIdentity.resolve(version_file=...)`.
  - Switch to `unexpected_exceptions="report"` where the hand-built `expected_exceptions` tuples allow it.
  - `--dry-run` uses `VerbSpec.dry_run`; remove the ~10 `getattr(args, "dry_run", False)` sites.
  - Replace the two `_invoke` subprocess helpers with `invoke_script`.
  - Replace the conftest marker/collection wiring with the plugin.
  - Re-sync monitor-task's surface to schema 7.
  - Bring `install-host` and `scp-api` under the surface lifecycle with real catalogs.
  - Run `cli-extended surface pack` plus the review skill, and record findings.
  - Run `cli-extended audit` with no `fail`.
- **debian-install-v2:**
  - The same identity, boundary and test-helper absorption.
  - Remote bootstrap fetches the pinned, sha256-verified cli-extended wheel from its release manifest and zipimports it, replacing the `CLI_LIBRARY_SUBTREE` download.
  - The local `debian-install-v2.py` drops its `sys.path` fallback in favour of the installed library (CX-D3).
- **Gates:** both scripts' registered `suite` lanes must be green.

## W10 — planned adoptions (filed, not executed this session)

Each entry goes in the tool's own backlog (cross-repo convention). The common shape for every tool:

- declare `cli-extended>=0.2` and remove vendoring;
- run `CliIdentity.resolve`;
- set `unexpected_exceptions="report"`;
- adopt the surface lifecycle with a review and findings file;
- move `.claude/skills/*` into package data and call `register_skills_verbs`;
- add `doctor` where the tool has external dependencies;
- `cli-extended audit` passes;
- the tool's own gate passes.

| Order | Tool | Backlog | Tool-specific notes |
|---|---|---|---|
| ② | cmru | `cmru/KNOWN_ISSUES_TODO_BACKLOG.md` | S-CLI.9 moves to the surface lifecycle. Consolidate 5 registries (cli, agent, controller, handlers, tester). `parse_target_names` becomes `SelectorList`. Installer `[[installer.wheels]]` installs cli-extended first with `--no-index` (CX-D2). |
| ③ | nyxloom | `nyxloom/nyxloom-trove/4-backlog-inbox.md` | Drop `_invoke`/`_verb`/`_leaf`/`--traceback` in favour of W1. The `validate=` callbacks become W2 constraints where expressible. The wheel's `.dockerignore` lesson (P112) applies again. |
| ④ | cgprofile | `scripts/cgroup-profiler/nyxloom-trove/backlog/` | Its `doctor` moves to W5. |
| ⑤ | pwmcp | `pwmcp/KNOWN_ISSUES_TODO_BACKLOG.md` | 25-line client CLI; also `run-gate.py`/`build-push.py` scripts. |
| ⑥ | assay | `assay/nyxloom-trove/4-backlog.md` | Reword A-005. The zipapp bundles `cli_extended`. 2.2k-line parser with injected streams. |
| ⑦ | run-gate | `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` | 11k-line single module; full grammar re-registration (CX-D12). Also fix the test that wrote an `assay` shim into the real `~/.local/bin` (`tests/test_run_gate.py:359`). |
| ⑧ | ciu | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` | 2.4k-line manual dispatch; `ciu skills` and `ciu doctor` per SPEC-V8 §770 become the shared verbs. |
| — | dstdns | `dstdns` decisions/backlog | env-setup and cmru installer: install cli-extended first, `--no-index --find-links` (CX-D2). |

## Risks

- **Two copies in the shared venv** until ② lands (cmru's editable finder versus the installed wheel). This is verified at W8 deploy.
- **Mutation gate cost.** R2 currently takes ~3 h for ~1,000 mutants. The wave roughly doubles the source, so R2 runs once on the integrated branch with the lane budget raised, not per package.
- **Schema 7** invalidates every existing manifest, which today is only Netcup's monitor-task. W9 re-syncs it.
