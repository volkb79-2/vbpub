# PWMCP — known issues, TODO, and backlog

This file records consumer-facing product gaps for later triage. Normative
behavior remains in the product specification and consumer documentation.

Last updated: 2026-10-15 (PWMCP-01 implemented; PWMCP-02 fixed).

## PWMCP-01 — Provide a rendered-page inspection command

**Status: IMPLEMENTED 2026-10-15; release-gate verification pending.**

Consumers needed one page's JavaScript-rendered text or screenshot without
writing a browser client. Before this change, the CLI exposed only `doctor` and
`contract`; consumers had to call MCP tools directly or install the
release-bundled Python client plus matching Playwright package and write a
`BrowserLease` script. Raw MCP SDK snippets also coupled consumers to transport
and SDK API details.

Implemented as a registered `pwmcp inspect URL` verb using `BrowserLease` and
the matching Playwright package from the release bundle. It returns rendered
body text, an ARIA snapshot, or a screenshot; supports navigation and visible
selector waits; and closes the managed lease on exit. It does not require local
browser binaries. The standalone CMRU install also ships this CLI and makes
the service available to agents over the existing internal network.

The consumer examples, output formats, wait states, standalone install/update
flow, and internal MCP endpoint are documented in the README and consumer
guide. PWMCP's release gate has not been run in this work session.

## PWMCP-02 — Adopt cli-extended (unified adoption, order 5 of 8)

**Status: FIXED 2026-10-07 (branch `pwmcp-clix`; controller merges and releases).**

**Observed / fix / oracle (evidence in [`docs/PWMCP-02-REPORT.md`](docs/PWMCP-02-REPORT.md)).**

- **Observed:** the mechanism section below (hand-rolled argparse with a `choices` list, a literal `"0.1.0"` fallback, tracebacks from `contract`/`doctor`, `dependencies = []`, four scripts with their own parsers and a `sys.path.insert` toward `cmru/src`).
- **Fix:** the client CLI is registered on `CliRegistry` (`contract`, shared `doctor` with `contract`/`playwright`/`skills` checks, `skills` via `register_skills_verbs`), identity through `CliIdentity.resolve` (`client/src/pwmcp_client/VERSION` is the wheel's dynamic version, metadata and file must agree), `unexpected_exceptions="report"`, domain failures as `CliFailure`; `cli-extended>=0.3.0` is declared in `client/pyproject.toml` with its floor reason; the skill moved to package data; surface lifecycle configured in `client/pyproject.toml` (`docs/CLI-SPEC.md`, `docs/cli-review.toml` with 59 cases, `docs/cli-surface.json`); `build-push.py` and the three `scripts/` files register a single-command CLI through `scripts/_vars.py` `script_cli` (`--dry-run`, no `--yes`: non-interactive CMRU steps) and import the installed `cli_extended` and `cmru` (the `sys.path.insert` toward `cmru/src` is gone: cmru's own wheel requires `cli-extended`, so any interpreter that runs these scripts under CMRU has both); `cli-extended` added to pwmcp's `depends_on` in `cmru.orchestration.toml`.
- **Oracle:** `./run-gate.py r1` PASS at 100% line and branch coverage of the judged targets; `cli-extended audit` 15 pass / 0 fail / 0 manual; `cli-extended surface check` passes; `assert_cli_contract` against the real `python -m pwmcp_client.cli`; plants (exit code, vendoring, `sys.path`, literal version fallback, stale surface) each fail a named test. Intentional behavior changes are listed in the report.
- **Not covered / follow-ups:** deploy of a released pwmcp (client wheel plus bundle) is the controller's; cmru KI-70 (preflight does not read the nested `client/pyproject.toml`, so the `depends_on` edge is declared by hand); CLI-EXT-32 (audit AC-25 piecewise-path gap). PWMCP-01 is implemented separately in the 2026-10-15 instance work; its release-gate verification is pending.

Original plan (filed 2026-10-05 by the cli-extended unified-adoption program, W10):

**Source documents.** [`libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md`](../libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md) (decisions CX-D1..CX-D12, section "W10 - planned adoptions") and [`libraries/cli-extended/docs/ADOPTION-CHECKLIST.md`](../libraries/cli-extended/docs/ADOPTION-CHECKLIST.md) (AC-01..AC-25). **Dependency:** cli-extended 0.2.0 released first (W8, the controller). Not executed in the program's session. Land this before PWMCP-01 so the proposed `pwmcp inspect` verb is registered with the library from the start instead of added to a hand-rolled parser.

**Observed mechanism (verified in source).** The consumer-facing CLI is the 25-line `pwmcp` console script of the `pwmcp-client` wheel (`client/pyproject.toml`: `dependencies = []`, `[project.scripts] pwmcp = "pwmcp_client.cli:main"`). No file under `client/`, `scripts/` or `build-push.py` references `cli_extended`.

- Parser and help (AC-03, AC-04): `client/src/pwmcp_client/cli.py:1-25` builds a `PwmcpArgumentParser` with a positional `command` restricted to `choices=["doctor", "contract"]`, `--version` and `--contract` (default `http://pwmcp:3000/contract`). `client/src/pwmcp_client/diagnostics.py:15-26` defines `PwmcpArgumentParser(argparse.ArgumentParser)` overriding `format_help`, `format_usage` and `error`, plus `cli_headline()` (`:11`, "PWMCP <version> — client utility"). The two verbs are a choice list, not registered verbs; `doctor` is hand-written (`cli.py:21-23`) and prints a single `ok release=... playwright=... ws=...` line.
- Version (AC-01): `client/src/pwmcp_client/__init__.py:3-10` reads `importlib.metadata.version("pwmcp-client")` and falls back to the literal `"0.1.0"` (a hard-coded fallback, exactly the AC-01 pattern); `cli.py:6,15` and `diagnostics.py:8,12` consume `__version__`. The client's `session.py:33` also reads the installed Playwright version through `importlib.metadata`; that is a compatibility check against the server contract, not the tool's own version, and stays.
- Exception boundary (AC-10, AC-11): no top-level wrapper and no `--traceback`; `cli.py` lets `load_contract` and `verify_installed_playwright` errors propagate as tracebacks (`PwmcpError`, `PwmcpUnavailable`, `VersionMismatch` at `session.py:12,16,20` are the domain errors to map to `CliFailure`). The one `except Exception` is `session.py:87`, a cleanup-and-re-raise around `connect` that stays.
- `--dry-run` / `--yes` (AC-05, AC-12): none found in the client CLI. `build-push.py:202-206` has a stand-alone `argparse.ArgumentParser` with a required exclusive `--build`/`--push` group (push is mutating and has no confirmation or dry-run; decide `dry_run=True` or a recorded reason).
- Scripts (AC-03, AC-25): `build-push.py:33,35`, `scripts/build-bundle.py:21,35` and `scripts/publish-bundle.py:27,32` use `sys.path.insert` to reach `pwmcp/scripts` and `cmru/src` (cmru's own source), none toward `libraries/cli-extended`; `scripts/resolve-playwright-version.py:658` has its own `argparse` parser. Per CX-D3 these scripts import the installed `cli_extended` and their gate lanes keep `PYTHONPATH` at the worktree source; the cmru-source path hacks are a separate hazard to track with cmru's own library packaging.
- Gate PYTHONPATH (AC-25): `assay.toml:8` and `:47` set `PYTHONPATH = "client/src"` for the test lanes; the lanes must also see the library (installed in the tester image from the released wheel, or per-lane CX-D3 exemption).
- `run-gate.py` (the table's "also `run-gate.py`"): `pwmcp/run-gate.py` is a symlink to `../run-gate-project/run-gate.py` (verified with `ls -l`), so it is not a pwmcp-owned script; it is adopted once, under the run-gate entry (order 7).
- Skills (AC-19): `.claude/skills/pwmcp-fetch/SKILL.md` is the only source tree; move to package data in `pwmcp-client` and call `register_skills_verbs`.
- Doctor (AC-20): `pwmcp doctor` exists as a hand-written choice; it becomes the shared `doctor` verb with named checks (contract reachable, release matches, Playwright version matches), keeping the `ok release=...` output in `--json`.
- Tests (AC-21, AC-23): `client/tests/test_client.py:35,43` call `main()` and `main(["--version"])` in-process; no subprocess helper found.

**Common shape (tick each, cite the AC row).**

- [ ] `pwmcp-client` declares `cli-extended>=0.2.0` in `[project].dependencies` (currently `[]`); no vendoring (AC-24).
- [ ] `CliIdentity.resolve(...)` replaces the `"0.1.0"` fallback (AC-01, AC-02).
- [ ] Register `doctor` and `contract` as verbs; delete `PwmcpArgumentParser` (AC-03, AC-04, AC-07).
- [ ] `unexpected_exceptions="report"`; domain errors as `CliFailure` (AC-10, AC-11).
- [ ] Surface lifecycle: review/manifest/spec, `surface check` (AC-16, AC-17, AC-18).
- [ ] Skills via `register_skills_verbs` (AC-19); `doctor` as the shared verb (AC-20).
- [ ] `build-push.py` and `scripts/resolve-playwright-version.py` registered (or recorded as internal scripts with a reason); installed-library import, CX-D3 (AC-24, AC-25).
- [ ] Tests: `assert_cli_contract`, `invoke_script` where subprocess is needed (AC-21, AC-23).

**Acceptance.** `cli-extended audit` reports no `fail`; `cli-extended surface check` passes; pwmcp's own registered gate passes (`run-gate.toml`, `assay.toml`); a released pwmcp (client wheel plus server bundle) is deployed. The release bundle's client install instructions work offline with `--no-index --find-links` and the cli-extended wheel (CX-D2).

**Oracles.** `pwmcp --version`, `pwmcp doctor` and `pwmcp contract` keep their observable output on the golden fixtures in `client/tests/`; installing the built `pwmcp-client` wheel without the library fails with the dependency named; a controlled wrong implementation that keeps the `"0.1.0"` literal fails a test that removes the metadata and expects `VersionLookupError`.
