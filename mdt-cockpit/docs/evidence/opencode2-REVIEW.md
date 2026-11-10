<!-- origin: 2026-11-08; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/opencode2-REVIEW.md -->
<!-- relation: OpenCode packaging review; verbatim source text follows -->

# Review: 53e191341 feat(mdt-cockpit): ship OpenCode 2 via @opencode/cli platform packages

Reviewer: independent session, 2026-11-08. Read-only on the worktree; all trials in
<controller-scratch>/rv-oc2/ (clone `r`, worktrees `oc` = origin/main + this commit only, `mg` = oc merged with codex-image).
No installs, no builds; `npm view` (registry read) only.

## Verdict: MERGE, after codex-image-20261108, rebased. No blocking findings.

Six non-blocking items below (N1-N6); N1 and N2 are worth doing in the rebase.

## 1. How OpenCode is installed after the commit (verified)

- Staging: `OPENCODE_VER` = `@opencode/cli` npm `latest` (registry now: 2.2.6; `opencode-ai` latest 1.19.5, still receiving 1.x releases).
  Explicit `OPENCODE_VERSION` pin is checked as a published `@opencode/cli` release.
- Installer (`install_ai_cli_tools.py:321-346`): `npm install -g @opencode/cli-linux-<x64-baseline|arm64>[-musl]@<OPENCODE_VER>`, then symlinks
  `<prefix>/lib/node_modules/<pkg>/bin/opencode` to `/opt/mdt/npm/bin/opencode`.
- Package naming is right. `@opencode/cli@2.2.6` `optionalDependencies` lists exactly `cli-linux-{x64,x64-baseline,x64-musl,x64-baseline-musl,arm64,arm64-musl}` (+darwin/windows),
  all pinned to the launcher's own version. Platform packages have `os:[linux]`, `cpu:[x64|arm64]`, NO `bin` field and NO `libc` field; the ELF is at `bin/opencode`
  (confirmed in the copy under `/opt/mdt/npm/lib/node_modules/@opencode/cli/node_modules/@opencode/cli-linux-x64-baseline/bin/opencode`).
  The installer's `bin/opencode` expectation is therefore correct; the missing `bin` field is why the explicit `link_binary` is needed (it is there).
- IMPORTANT difference from this devcontainer: here `/opt/mdt/npm/bin/opencode -> .../@opencode/cli/bin/opencode.exe` is the LAUNCHER layout
  (meta package + postinstall hardlink; AVX2 `cli-linux-x64` selected, `opencode`+`opencode2` aliases). The image layout produced by this commit is different
  (`@opencode/cli-linux-x64-baseline/bin/opencode`, no `opencode2` alias). The local copy therefore proves the package shape, not the installer's end state.
  I replicated the end state: symlinked the baseline platform binary as `opencode` in scratch; `--version` = `opencode v2.2.6`, `run --help` shows `-m`, `--format json`, `--auto`, `--standalone`.
- Wrong-platform: x86_64 vs aarch64 cannot be silently crossed (npm EBADPLATFORM from `os`/`cpu`). musl vs glibc CAN: the musl packages carry no `libc` field, so npm
  installs either variant anywhere; only `platform.libc_ver()` guards it. On a musl host CPython's `libc_ver()` most likely returns `('', '')` (no glibc strings, `libc.musl-*.so` does not match its regex),
  which would select the glibc package and install "successfully". Pre-existing (identical code for the 1.x names) and the image is Debian trixie, so latent. See N3.
- Baseline-only on x86_64 is deliberate and documented (no AVX2 probing of the build host); costs some speed on AVX2 CPUs, deterministic across hosts. Fine.
- Version pin source: single, `_resolve_versions()` -> `tool-versions.env`/manifest. The staged version is the launcher's; the installed platform package is only guaranteed
  equal by npm version syntax. See N1/N2.

## 2. Tests and docs

Run in scratch (`oc`, this commit alone on origin/main), nice -n 10, one file at a time:
test_ai_cli_versions 75 passed; test_docs_in_sync 26 passed; test_install_ai_cli_tools 43 passed; test_oci_tool_staging 90 passed.
`tests/runtime/test_ai_cli_update.py` needs the built image (not run). I re-derived its new shell path logic against a fake `/opt/mdt/npm` tree with the image layout:
`package=@opencode/cli-linux-x64-baseline`, matches `^@opencode/cli-linux-`. It would NOT match this devcontainer's launcher layout (`@opencode/cli`), which is irrelevant to the image.

Hand mutations (11, applied one at a time to the scratch copy, reverted): killed 7, survived 4.
| # | Mutation | Result |
|---|---|---|
| M1 | installer scope `@opencode` -> `@opencodex` | killed (TestInstallOpencode::test_command_construction) |
| M2 | staging package back to `opencode-ai` | killed (test_stage_resolves_opencode_from_npm_package) |
| M3 | tripwire `theirs > ours` -> `>=` | killed, but only incidentally (test_gdu_dust_ov_dry... where both fakes return 0.0.1); no test names the equal-major case |
| M4 | drop the explicit-pin early return (`stage_tool_artifacts.py:625-626`) | SURVIVED: no test sends a pin through `_resolve_opencode_version` |
| M5 | musl suffix dropped (`install_ai_cli_tools.py:334`) | SURVIVED: no musl test (pre-existing gap) |
| M6 | x64-baseline -> x64 | killed |
| M7 | binary path `bin/opencode` -> `bin/opencode.exe` | killed |
| M8 | unreadable-legacy swallow -> raise | killed (test_opencode_package_move_check_is_not_a_hard_dependency) |
| M9 | size-report glob reverted to `opencode-linux-*` | SURVIVED: nothing tests the `LEAVES` globs |
| M10 | README update command reverted | SURVIVED: `test_docs_in_sync` pins only DESIGN-GUIDE for that string (README line is unguarded) |
| M11 | `_npm_major` returns the minor | killed |

Docs accuracy (README.md:212-218, DESIGN-GUIDE.md:560): package names, update command and the "launcher picks baseline-vs-AVX2 from the installing host's /proc/cpuinfo" statement
match `postinstall.mjs` (read). "needs npm script approval" is plausible: npm 11.20.0 has `allow-scripts`/`strict-allow-scripts` (not enforced here, since this devcontainer's postinstall ran).
Inaccuracies: see N4.

## 3. Dockerfile / manifest

The commit does not touch the Dockerfile. The opencode manifest probe (Dockerfile:1065-1068) is `opencode --version 2>&1 | head -1 || true`, i.e. the same
never-fails pattern the codex job just fixed for claude and codex only (codex-image Dockerfile hunk at ~1139-1156: `v="$(tool --version 2>&1)" || fail; test -n; echo ${v%%$'\n'*}`).
A broken opencode (for example the wrong-libc ELF: "No such file or directory" from a missing `/lib/ld-musl-*`) would be recorded as an error string and the build would pass.
Normalisation itself is fine: `normalise_version('opencode','opencode v2.2.6') == '2.2.6'` (verified), a bare 'v2.2.6' too. See N2.

## 4. OpenCode 2 CLI contract used by the operator

`opencode-od` = `exec opencode --standalone "$@"`; verified on 2.2.6: `--standalone`, `run -m provider/model`, `--auto`, `--format <default|json>` all exist, and the platform binary behaves
identically when invoked through an `opencode` symlink (no argv[0] or `opencode.exe` dependence seen). `~/.config/opencode/opencode.jsonc` (`providers`, `@opencode/ai/providers/openai-compatible`)
is the v2 schema; the commit changes none of that. Data paths (`~/.local/share/opencode/opencode.db`, `~/.local/state/opencode/service.json`) are inside the already documented persistence dirs.
The one behavioural delta for an image user: no `opencode2` alias (the launcher declares `opencode` and `opencode2`; the direct platform install exposes only `opencode`).
Nothing in the repo uses `opencode2` (grep). Nothing breaks the wrapper.

## Findings (all non-blocking)

N1 (should, in the rebase) staging checks only that `@opencode/cli@X` exists, but the installer installs `@opencode/cli-linux-*@X`. Registry times show the platform packages land
1-5 minutes AFTER the launcher (2.2.4: cli 16:53:18, baseline 16:55:37, x64 16:54:10; 2.2.6: cli 23:33:40, baseline 23:35:14, x64 23:38:54), the launcher's own postinstall even
documents the race. A staging run in that window records X and the image build fails with ETARGET (loud, retry works, but a flaky red build). Fix: in `_resolve_opencode_version`
also require `@opencode/cli-linux-x64-baseline@X` and `@opencode/cli-linux-arm64@X` to exist (via the existing `_fetch_json(registry/<pkg>/<ver>)` as in `_resolve_pinned_npm_version`), and say "platform package not published yet" in the StageError.
No post-install check that the installed package is X: after the merge, add `verify_opencode_install(prefix, version)` beside `verify_codex_install`: read
`<prefix>/lib/node_modules/<pkg>/package.json` `version == OPENCODE_VER`, and reuse the new `require_executable_binary(binary, ...)` from codex-image instead of `require_file` (`install_ai_cli_tools.py:346`; it does not check the exec bit).

N2 (should) extend the codex-image guarded manifest probe to opencode: Dockerfile:1066 -> `v="$(opencode --version 2>&1)" || { ...; exit 1; }; test -n "${v}" || ...; echo "opencode=${v%%$'\n'*}"`, and add `"opencode"`
to the `@pytest.mark.parametrize("tool", ["claude", "codex"])` list in the codex job's `test_a_broken_agent_cli_fails_the_manifest_step` (its regex is already generic on the tool name).
Also update the stale fixture `VERSION_BANNERS["opencode"] = ("1.19.4", "1.19.4")` (`test_oci_tool_staging.py:1041`) to the real 2.x banner `("opencode v2.2.6", "2.2.6")`.

N3 (nit, pre-existing) musl detection: `platform.libc_ver()[0] == "musl"` is likely never true on Alpine. Use `Path("/etc/alpine-release").exists()` or `ldd --version` text like OpenCode's own postinstall, and add a musl case to `TestInstallOpencode` (M5 survived). Image is glibc so not urgent.

N4 (nit, docs) DESIGN-GUIDE.md:560 keeps "yes (1.19.3 to 1.19.2 and back, measured)", a measurement of the retired 1.x package, for a row that now describes 2.x, and cites `opencode upgrade --major`,
a flag that is not in the 2.2.6 `upgrade --help` (flags: only `--method/-m`; I did not execute an upgrade). Either re-measure on the 2.x image with the runtime test and update the sentence, or mark the cell "1.x measurement; 2.x covered by tests/runtime/test_ai_cli_update.py".
The test docstring "measured on 1.19.5 and 2.2.6 alike" is likewise not reproducible without running `upgrade`; I could not verify it safely. README line 218 prose is long but accurate.

N5 (nit, tests) M4/M9/M10 survivors: add one test that an explicit pin (`"2.0.0"`) skips the tripwire even when the legacy fake says 3.0.0; add `LEAVES` glob check or drop it; add the README string to the `test_docs_in_sync` list.
M3 is only killed by an unrelated test: add an explicit "same major does not raise" assertion.

N6 (nit, scope) no CHANGES.md entry for a user-visible 1.x -> 2.x jump (v2 reads `opencode.jsonc` with a `providers` schema and keeps sessions in `opencode.db`; existing users' persisted 1.x config/data may need migration). The codex-image branch adds CHANGES entries; add one here after the rebase. Separately (out of scope) `nyxloom/infra/agent-cli/Dockerfile:62` and its README still pin `opencode-ai@1.18.1`.

## Merge-order and conflict analysis (codex-image-20261108 @ dd7669c4d vs this commit)

codex-image is based on ac7b97e9a (older than origin/main 11b4a7c34). Trial `git merge-tree --write-tree` and a scratch merge of (origin/main + 53e191341) with codex-image:

Textual conflicts in files this commit touches: exactly ONE.
- `mdt-cockpit/scripts/test_oci_tool_staging.py`, end of file: codex-image appends `test_a_broken_agent_cli_fails_the_manifest_step` (+ parametrize) after `test_the_dockerfile_extraction_commands_record_one_bare_version_per_tool`;
  this commit appends the "OpenCode moved npm packages once" block (`_opencode_registry`, 4 tests) at the same spot. Pure adjacent-append. Resolution: keep both blocks (codex block first, then the opencode block, one blank-line pair between). Nothing else in that file overlaps
  (codex edits `_manifest_commands`, new `_manifest_binary`, the one `command.split()[0]` loop at ~1120; this commit edits lines ~358, ~378, ~626 and the EOF block).
- `mdt-cockpit/CHANGES.md` also conflicts, but NOT because of this commit: it is codex-image vs origin/main (the commit does not touch CHANGES.md). Same with the full branch (`local-integrate-20261108` vs codex-image): only CHANGES.md and `test_oci_tool_staging.py`.

Auto-merged cleanly (distinct hunks): `install_ai_cli_tools.py` (codex removes `install_binaries_from_archive`, rewrites `install_codex`; this commit edits only the OpenCode block ~321-335), `test_install_ai_cli_tools.py`, `README.md`
(codex edits the `codex` bullet, this commit the OpenCode sentence and update line), `docs/DESIGN-GUIDE.md` (codex adds paragraphs ~551; this commit edits the opencode table row ~560).

Semantic interplay: none breaking. Result of the scratch merge (conflicts resolved as above, CHANGES taken from one side): test_oci_tool_staging 94 passed, test_install_ai_cli_tools 62 passed,
test_ai_cli_versions 75 passed, test_docs_in_sync 26 passed. The codex Dockerfile hunk is outside the opencode lines (only claude/codex probes); the codex installer changes are independent of `install_opencode`.
The only interaction is an opportunity, not a conflict: after the merge, the new helpers (`require_executable_binary`, `json` import, guarded-probe regex) can be reused for N1/N2.

Recommendation: land codex-image first (it also owns CHANGES.md, whose conflict with origin/main it must resolve anyway), then rebase 53e191341 on top, resolve the EOF append by keeping both blocks,
fold N1/N2 (and N6's CHANGES line) into the rebased commit or a follow-up, rerun the four test files once each under nice -n 10.
