---
kind: backlog-entry
schema_version: 1
id: CP-17
title: "Adopt cli-extended (unified adoption, order 4 of 8)"
status: open
type: "feature"
severity: "medium"
component: "cli"
provenance: "cli-extended unified-adoption program W10, 2026-10-05 (libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md, CX-D11)"
filed_by: "Claude Sonnet (W10 implementer)"
spec_owner: "libraries/cli-extended/docs/ADOPTION-CHECKLIST.md"
filed_date: "2026-10-05"
---

**Status: PLANNED (filed 2026-10-05 by cli-extended unified-adoption W10; adoption order 4 of 8).**

**Source documents.** `libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md` (decisions CX-D1..CX-D12, section "W10 - planned adoptions") and `libraries/cli-extended/docs/ADOPTION-CHECKLIST.md` (AC-01..AC-25). **Dependency:** cli-extended 0.2.0 released first (W8, the controller). Not executed in the program's session.

**Observed mechanism (verified in source; cgprofile is a script, not a wheel).** `cgprofile` is a single-file CLI (`cgprofile.py`, 1599 lines) launched by the `cgprofile` bash wrapper. It is deliberately stdlib-only on the collector tier (`pyproject.toml` `dependencies = []` with the comment that nothing needed at collection time may be listed; `[tool.setuptools] py-modules = []` so the code is bound from the worktree and never installed). It has no `cli-extended` reference anywhere (`grep -rn "cli_extended\|cli-extended"` over `cgprofile.py`, `lib/`, `tests/`, `pyproject.toml`, `Dockerfile` found nothing).

Hand-rolled patterns an adoption replaces:

- Parser and help (AC-03, AC-04): `CgprofileArgumentParser(argparse.ArgumentParser)` at `cgprofile.py:63-78`, `build_parser()` at `:1390` with `add_subparsers` at `:1401` and `:1515`, 17 `add_parser(` verbs, a shared `_add_common` (`:1331`) and `_add_log_tail_args` (`:1372`), and the hand-written headline `cli_headline()` (`:59-60`).
- Version (AC-01): `cgprofile.py:44-52` reads `tomllib.load(pyproject.toml)["project"]["version"]` and passes it through `lib/version.py:runtime_version`, which prefers the `CGPROFILE_VERSION` environment variable stamped by the image build (`lib/version.py:12,19-26`; `docker-bake.hcl:22,39`); `lib/serve.py:59` repeats `CGPROFILE_VERSION = runtime_version("1.1.0")` with a literal source fallback. `--version` is declared at `cgprofile.py:1399`. `CliIdentity.resolve(..., version_file=...)` has to cover the image case (env-stamped) as well; if it cannot, that is a library finding to file against cli-extended rather than a local workaround.
- Exception boundary (AC-10, AC-11): `main()` at `cgprofile.py:1584-1597` catches `access.AccessError` and `targets_mod.TargetError` (each `_err(str(exc))`) and `KeyboardInterrupt` (return 130); there is no catch-all and no `--traceback`. `lib/serve.py` has five `except Exception` blocks at `:562, :1078, :1353, :2407, :2461` (each marked `# noqa: BLE001`), which are daemon-loop blast-radius guards, not CLI wrappers, and stay.
- `sys.path` hacks (AC-25): `cgprofile.py:47` `sys.path.insert(0, HERE)` (to import its own `lib/`) and `lib/damon.py:50` (`_DAMON_ANALYSIS_LIB`). Neither points at `libraries/cli-extended`; they are the script's own layout.
- `--dry-run` / `--yes` copies (AC-05, AC-12): none found in `cgprofile.py`; no confirm prompts (no `input(` found). Mutating verbs (`ctl stop`, `ctl gc`) need a review decision on `dry_run`/`--yes` or a recorded reason.
- Doctor (AC-20): `cmd_doctor` at `cgprofile.py:920`, parser at `:1441-1442` (`--helper-image`). This moves to the shared W5 `doctor` verb with its current sections (`access`, `reporting venv`, ...) as named checks.
- Skills (AC-19): `.claude/skills/cgprofile/SKILL.md` is a source tree; move it to package data and call `register_skills_verbs`. No `skills` verb exists today.
- Tests (AC-23): `tests/test_cli_diagnostics.py:29,40` use `subprocess.run` against the CLI; `tests/test_cgprofile.py` imports `cgprofile` as a module. Candidates for `invoke_script(home=...)` and `assert_cli_contract`.

**Design question to settle in the carve (not decided here).** CX-D1 says every tool takes a real wheel dependency, and CX-D3 gives scripts "import the installed `cli_extended`, gate lanes keep `PYTHONPATH` at the worktree source". cgprofile's collector tier runs on the bare system `python3` inside the privileged helper container and in gate containers with no venv (see the header of the `cgprofile` wrapper). The carve must say how `cli_extended` reaches that interpreter (image layer from the released wheel per CX-D2, or a zipimport entry per CX-D3) and must keep the collector's "nothing outside the standard library is needed at collection time" property true, since `cli_extended` is itself stdlib-only. If the answer needs a library change, file it against cli-extended.

**Common shape (tick each, cite the AC row).**

- [ ] Declare/provide `cli-extended>=0.2.0` per the design question above; no vendoring, no `PYTHONPATH` onto the library checkout in project sources (AC-24, AC-25).
- [ ] `CliIdentity.resolve(...)` replaces `cgprofile.py:44-52`, `lib/version.py` reader and `lib/serve.py:59` literal (AC-01, AC-02).
- [ ] Re-register the whole grammar (17 verbs, `ctl` sub-verbs) with `CliRegistry`/`VerbSpec`; delete `CgprofileArgumentParser` (AC-03, AC-04, AC-07).
- [ ] `CliRegistry(unexpected_exceptions="report")` (AC-10); domain errors as `CliFailure`/`expected_exceptions` (AC-11).
- [ ] Surface lifecycle: configure review/manifest/spec, `surface sync`, review catalog and findings, `surface check` (AC-16, AC-17, AC-18).
- [ ] Skills via `register_skills_verbs` (AC-19); `doctor` via the shared verb (AC-20).
- [ ] Tests: `assert_cli_contract` (AC-21), `invoke_script` (AC-23), pytest plugin if a catalog exists (AC-22).

**Acceptance.** `cli-extended audit` reports no `fail`; `cli-extended surface check` passes; cgprofile's own registered gate (`run-gate.toml`, tester image from `pyproject.toml`) passes; a released cgprofile version is deployed (merge + cmru release of the OCI image and wheel/script artifact + deployment, per the estate's "shipped" definition).

**Why cgprofile owns it.** The change is to cgprofile's parser, packaging and image layout; the library cannot do it. Related existing entries: none (`INDEX.md` has CP-1..CP-16 and no cli-extended entry).

**Oracles for the carver.** `cgprofile doctor` output sections are unchanged (golden compare); `cgprofile mark` still runs on the bare system `python3` with no venv present; a controlled wrong implementation that imports `cli_extended` only from the reporting venv must fail the bare-python3 `mark` oracle.
