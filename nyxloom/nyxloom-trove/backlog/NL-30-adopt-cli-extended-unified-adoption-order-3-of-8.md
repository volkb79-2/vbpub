---
kind: backlog-entry
schema_version: 1
id: NL-30
title: "Adopt cli-extended (unified adoption, order 3 of 8)"
status: open
type: "feature"
severity: "medium"
component: "cli"
provenance: "cli-extended unified-adoption program W10, 2026-10-05 (libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md, CX-D11)"
filed_by: "Claude Sonnet (W10 implementer)"
spec_owner: "libraries/cli-extended/docs/ADOPTION-CHECKLIST.md"
filed_date: "2026-10-05"
---

**Status: PLANNED (filed 2026-10-05 by cli-extended unified-adoption W10; adoption order 3 of 8).**

**Source documents.** `libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md` (decisions CX-D1..CX-D12, section "W10 - planned adoptions") and `libraries/cli-extended/docs/ADOPTION-CHECKLIST.md` (rows AC-01..AC-25). **Dependency:** cli-extended 0.2.0 must be released first (the controller does that in W8). Nothing here is executed in the program's session.

**Path correction.** The W10 table names `nyxloom/nyxloom-trove/4-backlog-inbox.md`. That file is nyxloom's un-carved idea inbox (`nyxloom-trove/nyxloom.toml:31`); managed entries live in `nyxloom-trove/backlog/` (`[backlog_entries] id_prefix = "NL"`, `nyxloom.toml:43-44`), so this entry is filed there.

**Observed mechanism (nyxloom already uses cli-extended, but by vendoring).** nyxloom adopted the library through a source copy, not a dependency, and keeps its own adapters around the runtime:

- Vendoring (AC-24, CX-D1): `nyxloom/pyproject.toml:48` maps `cli_extended = "../libraries/cli-extended/src/cli_extended"` via `package-dir`, and `:51-52` adds `../libraries/cli-extended/src` to `packages.find` with `include = ["nyxloom*", "cli_extended*"]`, so the nyxloom wheel embeds a copy of the library. The image build copies it too: `nyxloomd/Dockerfile:40` (`COPY libraries/cli-extended/src/cli_extended ...`), with the explanatory comments at `nyxloomd/ciu.compose.yml.j2:43` and `docker-bake.hcl:27`. `pyproject.toml` declares no `cli-extended` dependency.
- Gate path hack (AC-25): `assay.toml:40` and `:146` set `PYTHONPATH = "src:../libraries/cli-extended/src"`. Under CX-D3 a gate lane may keep a worktree-source path for the revision under test; decide per lane in the carve whether the tester image gets the wheel instead. No `sys.path.insert` toward `libraries/cli-extended` found in `src/`, `tools/` or `tests/` (the only `sys.path.insert` hits are `tools/extract_planner_corpus.py:47-48`, which point at nyxloom's own `src` and `tests`).
- Version reader (AC-01): `src/nyxloom/__init__.py:18-28` reads `importlib.metadata.version("nyxloom")` and falls back to the literal `"0.0.0+unknown"`; `src/nyxloom/daemon_entrypoint.py:8,22` and `src/nyxloom/cli_registry.py:25,31` consume `__version__`. `CliIdentity.resolve` replaces this and removes the literal fallback.
- Exception wrapper and `--traceback` (AC-10, AC-11): `src/nyxloom/cli_registry.py:37-41` defines `_traceback_option()`, `:86-112` defines `_invoke` whose `except Exception` (line 106, marked "census: process-boundary translation (P112)") prints `error: <exc>` and returns 1 unless `args.traceback`; `:172` installs it as a global option. `src/nyxloom/cli.py:1369` and `:1386` (the only two `traceback` mentions in that module) repeat `if getattr(args, 'traceback', False): raise` inside two decision handlers (the second is `cmd_discuss`). W1's `unexpected_exceptions="report"` plus the library `--traceback` replaces all of these.
- `_verb` / `_leaf` (AC-03): `src/nyxloom/cli_registry.py:115` and `:177` are the adapter factories that wrap handlers into `VerbSpec` and inject `args.runtime`; they should reduce to plain registrations once `_invoke` is gone.
- `validate=` callbacks (AC-06): `cli_registry.py:140` and `:207` plumb the parameter; the real guards are `_doctor_guard` (`:224`), `_backlog_new_guard` (`:242`), `_resync_guard` (`:251`), `_capability_refresh_guard` (`:260`), `_extract_guard` (`:269`, used at `:557`, `:587`, `:618`) and `_extract_report_guard` (`:324`, used at `:626`). `:263` is an explicit conflict message ("--emit-findings cannot be combined with --dry-run"). Convert each guard that expresses a requires/conflicts/one-of rule to a W2 `Requires` / `Conflicts` / `RequiresChoice` constraint; keep a guard only where the rule needs runtime state.
- `--dry-run` copies (AC-05, AC-12): `cli_registry.py:733` and `:739` declare their own `_opt("--dry-run", ...)` options. They collide with the library-owned `--dry-run` and must become `VerbSpec(dry_run=True)` (mutating verbs only).
- Skills (AC-19): `nyxloom/.claude/skills/` holds five source trees (`nyxloom-backlog`, `nyxloom-carve`, `nyxloom-dispatch`, `nyxloom-merge-p`, `nyxloom-pack`). Move them to package data and call `register_skills_verbs`; `.claude/skills` stops being the source of truth.
- Doctor (AC-20): `src/nyxloom/doctor.py` and `_doctor_guard` already exist as a hand-written verb; evaluate against the shared W5 `doctor` and keep nyxloom-specific checks as named checks.
- Test helpers (AC-23): no `def _invoke` helper and no subprocess-per-command CLI wrapper found in `nyxloom/tests/`; CLI coverage lives in `tests/test_cli_adoption.py`, `tests/test_cli.py` and the per-feature acceptance tests. Evaluate `assert_cli_contract` (AC-21) against `test_cli_adoption.py`.

**Common shape (tick each, cite the AC row).**

- [x] **DONE 2026-10-07 (NYX-CLIX, branch `mm-move`; floor is 0.3.0, not 0.2.0, see the reason in `pyproject.toml`).** Declare `cli-extended>=0.2.0` in `[project].dependencies`; delete the `package-dir` entry (`pyproject.toml:48`) and the `cli_extended*` include (`:51-52`) (AC-24). Oracle: the built wheel has no `cli_extended/` and carries `Requires-Dist: cli-extended>=0.3.0`; `tests/test_packaging_cli_extended.py` guards it.
- [x] **DONE 2026-10-07 (NYX-CLIX).** Remove the `PYTHONPATH` library checkout from `assay.toml:40,146`, or record the CX-D3 gate-lane exemption per lane (AC-25). Both lanes now use `PYTHONPATH = "src"` and import the tester-unified image's released cli_extended.
- [x] **DONE 2026-10-07 (NYX-CLIX; built and installed in the Dockerfile, image build itself not run here, only its wheel/install steps reproduced in a scratch venv).** Image build: replace `nyxloomd/Dockerfile:40` with a wheel install (CX-D2: release wheel, sha256, `--no-index --find-links`). Re-read the P112 lesson: the repository-root `.dockerignore` excluded `libraries/` and had to whitelist `libraries/cli-extended/src/cli_extended` (`nyxloom-trove/reports/nyxloom-P112-LOG.md:257`, `nyxloom-P112-REPORT.md:96-118`). Dropping the copy removes that whitelist's reason; the replacement install path must be checked against the same context filtering before the whitelist is deleted.
- [ ] `CliIdentity.resolve(...)` replaces `__init__.py:18-28` (AC-01, AC-02).
- [ ] `CliRegistry(unexpected_exceptions="report")`; delete `_invoke`'s wrapper, `_traceback_option` and the per-handler `traceback` re-raise (AC-10, AC-11).
- [ ] `validate=` guards become W2 constraints where expressible (AC-06); drop `_verb` / `_leaf` adapters (AC-03); own `--dry-run` options become `dry_run=True` (AC-05, AC-12).
- [ ] Surface lifecycle: `[tool.cli-extended]` review/manifest/spec configured, `cli-extended surface sync`, review catalog and findings file, `surface check` passes (AC-16, AC-17, AC-18).
- [ ] Skills as package data via `register_skills_verbs` (AC-19); `doctor` via the shared verb (AC-20).
- [ ] Tests: `assert_cli_contract` (AC-21), `cli_extended.pytest_plugin` when a catalog exists (AC-22).

**Acceptance.** `cli-extended audit` reports no `fail`; `cli-extended surface check` passes; nyxloom's own registered gate (`run-gate.py`, lanes in `run-gate.toml`) passes; a released nyxloom version is deployed (merge + cmru release + devcontainer install, per the estate's "shipped" definition).

**Why nyxloom owns it.** The migration is a change to nyxloom's packaging, image build, CLI registry and tests; the library cannot do it. The decision itself is the operator's (CX-D1, CX-D11).

**Oracles for the carver.** `pip download`/inspect of the built nyxloom wheel shows no `cli_extended/` package and a `Requires-Dist: cli-extended>=0.2.0`; `grep -rn "traceback" src/nyxloom/cli_registry.py src/nyxloom/cli.py` finds no hand-rolled handler re-raise; `nyxloom --help` and every verb help still render the same verbs (surface manifest diff reviewed); a controlled wrong implementation that leaves the vendored copy in place fails the wheel-contents check.
