# W10 report: file the planned cli-extended adoptions

Package W10 of the unified-adoption program. Docs-only: backlog entries, no code
changed. Branch `cli-ext-w10-backlogs`, worktree
`/workspaces/vbpub/.worktrees/cli-ext-w10-backlogs`. Every entry was written from
read-only greps of the tool's source in that worktree; "evidence" below counts the
`file:line` / `` `:line` `` tokens (counted by a regex over the added text, so approximate) in the entry.

## Per-tool table

| Order | Tool | Backlog file (all under the worktree) | Entry id | New / updated | Evidence cites |
|---|---|---|---|---|---|
| 2 | cmru | `cmru/KNOWN_ISSUES_TODO_BACKLOG.md` | KI-51 | new | 51 (version readers, 12 `CliRegistry(` sites, 10 `--dry-run` copies, 26 `except Exception`, vendoring, installer) |
| 3 | nyxloom | `nyxloom/nyxloom-trove/backlog/NL-30-adopt-cli-extended-unified-adoption-order-3-of-8.md` (+ regenerated `INDEX.md`) | NL-30 | new | 40 |
| 4 | cgprofile | `scripts/cgroup-profiler/nyxloom-trove/backlog/CP-17-adopt-cli-extended-unified-adoption-order-4-of-8.md` (+ regenerated `INDEX.md`) | CP-17 | new | 21 |
| 5 | pwmcp | `pwmcp/KNOWN_ISSUES_TODO_BACKLOG.md` | PWMCP-02 | new | 18 |
| 6 | assay | `assay/nyxloom-trove/4-backlog.md` (frontmatter line, "Later waves" list line, body) and `assay/nyxloom-trove/decisions.md` (A-005 row) | B143 | new backlog item; A-005 reworded in place | 26 |
| 7 | run-gate | `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` (two status-table rows + two bodies) | RG-82 (adoption), RG-83 (bug); filed as RG-81/RG-82, renumbered at the merge with main, which had taken RG-81 meanwhile | new | 18 (RG-82), 14 (RG-83) |
| 8 | ciu | `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` | CIU-114 | UPDATED (the existing one-line stub "adopt `cli-extended` for CLI grammer, usage(), wizard" was rewritten into the evidenced entry; no parallel entry) | 28 |

Ids were taken from each file's own sequence: nyxloom and cgprofile via
`nyxloom backlog new` (which allocated NL-30 and CP-17 and regenerated each
`INDEX.md`); the others by reading the file's last id (KI-50, PWMCP-01, B141/B142,
RG-80, CIU-125 and the existing CIU-114).

Every entry contains: title `Adopt cli-extended (unified adoption, order <n> of 8)`;
planned/open status; links to `PROGRAM-2026-10-UNIFIED-ADOPTION.md` and
`ADOPTION-CHECKLIST.md`; the common shape as a checklist with AC ids; tool-specific
notes with file:line evidence; acceptance (`cli-extended audit` with no `fail`,
`surface check` passes, the tool's own registered gate passes, a released version
deployed); and the dependency on cli-extended 0.2.0.

## Path corrections and deviations from the W10 table

1. **nyxloom:** the table names `nyxloom/nyxloom-trove/4-backlog-inbox.md`. That file
   is the un-carved idea inbox (`nyxloom-trove/nyxloom.toml:31`). Managed entries live
   in `nyxloom-trove/backlog/` with `id_prefix = "NL"` (`nyxloom.toml:43-44`), so the
   entry went there.
2. **cgprofile:** the table gives a directory; it is a managed backlog
   (`[backlog_entries] id_prefix = "CP"`), so a per-entry file was created.
3. **assay:** A-005 is not in `4-backlog.md`. It is a decision row in
   `assay/nyxloom-trove/decisions.md`; it was reworded there ("No third-party runtime
   dependencies", original text quoted, pointer to B143). The backlog item is B143.
   Other statements of the old wording were NOT touched and are listed in B143 as
   follow-ups: `assay/pyproject.toml` comment lines 20-23 and `:24`,
   `assay/nyxloom-trove/1-north-star.md:66`, README/CONSUMERS wording,
   `gate/tests/test_dependency_purity.py:177-178`, `gate/tests/test_standalone.py:7,2051`.
   Pre-existing gap noticed, not fixed: the frontmatter index of `4-backlog.md` has no
   line for B142 (the body has one); B143 was added after B141.
4. **cmru:** the table says "5 registries (cli, agent, controller, handlers, tester)".
   `grep -rn "CliRegistry(" cmru/src` finds 12 construction sites (listed in KI-51).
5. **pwmcp:** the table says "also `run-gate.py`/`build-push.py`". `pwmcp/run-gate.py`
   is a symlink to `../run-gate-project/run-gate.py` (so are `assay/run-gate.py` and
   `ciu/run-gate.py`), adopted once under RG-82. The real pwmcp scripts are
   `build-push.py`, `scripts/build-bundle.py`, `scripts/publish-bundle.py` and
   `scripts/resolve-playwright-version.py`.
6. **cgprofile and run-gate raise a design question** the program text does not settle
   (CX-D1 versus the stdlib-only, zero-install design of those tools; run-gate's integer
   `__revision__` versus the resolver's semver requirement). Both entries state the
   question for the carve and decide nothing. cmru's `get.py.tmpl:634` installs bundled
   wheels one at a time with `pip install --no-index`, which cannot resolve a
   `cli-extended` dependency; KI-51 and CIU-114 record the CX-D2 change.

## run-gate bug (RG-83)

`run-gate-project/tests/test_run_gate.py:355` (`install_fake_assay`, def at `:348`)
writes `<first $PATH entry>/assay` and `assay.real` (`:360`, `:362`) using
`shim_dir_of` (`:418-419`, `PATH.split(":")[0]`). It is only safe after a fake-docker
fixture has prepended a tmp dir (`:280`). `TestRG76AssayLaneImports._project`
(`:8424`, call at `:8455`) and the test at `:8504` call it without that, and in the
devcontainer the first `$PATH` entry is `/home/vscode/.local/bin`. Evidence that this
happened: `~/.local/bin/assay` (633 bytes, wrapper) and `~/.local/bin/assay.real`
(391 bytes) dated Oct 4 14:36; `assay.real` contains the `alpha/beta/gamma` inventory
JSON that `:8455` writes. Nothing was deleted or modified in `~/.local/bin`; I only
read the two files. Whether a genuine `assay` existed at that path before is not
determined. The entry proposes the fix (tmp bin dir and HOME, plus a guard fixture)
and ties it to `invoke_script(home=...)`.

## dstdns entry (draft, controller files it)

Not filed: `/workspaces/dstdns` was not touched.

> **Title:** Install cli-extended first in env-setup and the cmru installer (`--no-index --find-links`), per CX-D2
>
> **Context.** vbpub's cli-extended unified-adoption program (decisions CX-D1/CX-D2,
> `vbpub/libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md`) makes
> `cli-extended` a real wheel dependency of every vbpub CLI. It is released by cmru
> as a GitHub Release (`cli-extended-v<version>`, asset
> `cli_extended-<version>-py3-none-any.whl`, plus a `cli-extended-latest/latest.json`
> pointer carrying `version`, `tag`, `asset`, `sha256`, `url`). It is never published
> to PyPI, and the bare name is unclaimed there (404 verified 2026-10-05), so a plain
> `pip install <tool>.whl` that resolves the dependency from PyPI would be a
> dependency-confusion hole.
>
> **Required in dstdns.**
> 1. `env-setup` installs `cli-extended` first: download the wheel from the release,
>    verify its sha256 against `latest.json` / the release manifest, then install it
>    with `pip install --no-index --find-links <cache-dir> cli-extended`.
> 2. Every vbpub tool wheel (cmru, assay, ciu, run-gate, nyxloom, pwmcp-client,
>    cgprofile where packaged) is then installed with
>    `pip install --no-index --find-links <cache-dir> <tool>`, never with an index.
> 3. The cmru installer path (`get.py enroll`, `[[installer.wheels]]`) gets the same
>    order; vbpub tracks that change as cmru KI-51 and ciu CIU-114.
> 4. The assay `.pyz` bundles `cli_extended` itself, so a pip-less image that only
>    copies the `.pyz` needs nothing extra.
> 5. dstdns scripts that import a vbpub CLI library keep their gate lanes on
>    `PYTHONPATH` at the worktree source (CX-D3); installed environments use the wheel.
>
> **Acceptance.** A fresh devcontainer rebuild offline (no network beyond the cached
> release assets) installs the vbpub tools and `python -c "import cli_extended"`
> works; removing the cached cli-extended wheel makes the tool install fail naming
> the missing dependency instead of reaching PyPI.
>
> **Depends on:** cli-extended 0.2.0 released (vbpub W8); order of adoption
> cmru, nyxloom, cgprofile, pwmcp, assay, run-gate, ciu, so dstdns can switch the
> install order as soon as cmru's installer change ships.

## Gate and verification (what was actually run)

- `nyxloom backlog index` in `nyxloom/` and `scripts/cgroup-profiler/` after filing:
  re-running left `git diff` unchanged (one added row per `INDEX.md`).
- `nyxloom/tests/test_backlog_entries.py`, serial,
  `PYTHONPATH=src:../libraries/cli-extended/src flock <gate.lock> nice -n 19 ionice -c 3 python -m pytest tests/test_backlog_entries.py -q`:
  exit code 0 (log: scratchpad `w10-nl-tests.log`; the progress lines show only passes).
  That file exercises nyxloom's backlog-entry tooling against temporary projects; it
  does not parse the real `nyxloom-trove/backlog/`.
- For the other tools I grepped their `tests/`, `gate/tests/` and `tools/` for the
  backlog file names: matches exist only as docstring/comment mentions
  (assay `tests/core/test_runner_run_lane.py:821`; ciu `tests/tests/*` docstrings).
  No test parses those files, so no further test was run. No r2/mutation lane was run
  and no registered gate was run (docs-only; the shared host runs a mutation campaign).
- Not run: any link checker over the edited files. Relative links in the new cmru and
  pwmcp and run-gate and ciu entries point at
  `../libraries/cli-extended/docs/...`, which resolves from each project directory.

## Honest limits

- Line numbers are from the worktree at branch start (worktree HEAD `8cbb65d15`). A
  reviewer should re-grep rather than trust a number.
- `~/.local/bin` was only read. No file outside the worktree and the scratchpad was
  written, other than whatever `nyxloom backlog new/index` wrote inside the worktree.
