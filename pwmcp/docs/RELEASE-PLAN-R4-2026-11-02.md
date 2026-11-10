<!-- origin: 2026-11-02; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/PWMCP-R4-RELEASE-PLAN-2026-11-02.md -->
<!-- relation: PWMCP r4 release plan -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# PWMCP r4 release plan — 2026-11-02

Read-only analysis. Base: `main` = `9a828c573`. The last release is `pwmcp-v1.64.0-r3` (tag on `746f7bb40`, 2026-10-21). Since then, 32 commits touch `pwmcp/`.

## 1. What r4 contains and how it is cut

**Content.** r4 is everything on `main` under `pwmcp/` since r3. That is:

- the OCR sidecar (PWMCP-11 phase 1, opt-in, off by default);
- the user-agent option (PWMCP-09);
- the R2 survivor hardening (tests only, plus one behaviour-neutral reorder in `containers/ocr/ocr_service.py` `_send`);
- the test-dependency policy (`PWMCP_REQUIRE_TEST_DEPS=1` in the gate lanes).

`CHANGES.md [Unreleased]` already has `### Added` (OCR, user agent) and `### Testing` (hardening). Both are groups CMRU accepts (`cmru/src/cmru/changelog.py:277`).

**Artifacts.** These follow from `pwmcp/cmru.toml` and `build-push.py`:

- `docker buildx bake all` builds both targets, `pwmcp-latest` and the NEW `pwmcp-ocr` (`docker-bake.hcl` group `all`). It pushes `ghcr.io/volkb79-2/pwmcp:<tag>` and `ghcr.io/volkb79-2/pwmcp-ocr:<tag>`.
- The stack bundle `pwmcp-v<ver>.tar.xz` is published with the wheels. `get.py` is re-rendered by `cmru get-py`.
- GHCR visibility is mirrored for `GHCR_PACKAGE_NAMES=pwmcp,pwmcp-ocr`. The resolver writes this into `cmru.vars`, and `build-push.py` uses the same default.
- **The OCR image does not need a corpus.** It is built from `containers/ocr/Dockerfile`: a pinned Debian digest, `apt` Tesseract, and only `ocr_service.py` copied in.

**Version.** The version strategy is `external:PWMCP_VERSION`. The committed projection (`docker-bake.hcl`, `ciu.defaults.toml.j2` including `[pwmcp.ocr.image] tag`, `pwmcp.contract.json`) still says `1.64.0-r3`. r3 practice was a separate prep commit made with `scripts/resolve-playwright-version.py --refresh` (`70ef901f1`), followed by `cmru release pwmcp`.

The refresh picks the newest Playwright version that npm, PyPI and the MCR base all share, subject to an age window. It then takes the next `r<N>` from the tags. Upstream evidence read on 2026-11-02:

- PyPI `playwright 1.64.1` was uploaded 2026-10-22, so it is 11 days old.
- npm `1.64.1` is dated 2026-10-14.

The resolver's built-in default window is 2 days. With that default, it would select `1.64.1` and the release would be `1.64.1-r1`.

The estate freshness rule requires 14 days for a dependency (user-global CLAUDE.md, 2026-09-12). "Our own tools have no age window" does not apply here, because Playwright is upstream. With `--age-window-days 14`, the refresh selects **`1.64.0`, so the release is `1.64.0-r4`**. This holds until 2026-11-05. From 2026-11-05 on, 1.64.1 clears 14 days and the same command produces `1.64.1-r1`.

The Debian base digest has no window, by design (PWMCP-11). The refresh also refreshes that digest and the Playwright base digest.

**Commands.** The full step list is in section 5.

- Prep: `python3 scripts/resolve-playwright-version.py --refresh --age-window-days 14` (from `pwmcp/`).
- Release: `cmru release pwmcp`, after `cmru release pwmcp --dry-run` shows `pwmcp-v1.64.0-r4`.
- `--set-version 1.64.0-r4` is optional, as a guard that must agree with the external version. Try it on the dry-run first and drop it if CMRU refuses it for an external-strategy project.
- Never pass `--skip-gates`.
- Never use `--resume` without the recorded plan (memory: the mistaken nyxloom v1.0.0).

## 2. Blockers

### 2a. Nothing in the release path needs the corpus

On `main`, neither the OCR image build, the bundle, nor the tests reference the console corpus. A grep of `main:pwmcp` for corpus finds only `tools/glyphgrid` and `tools/console-capture` documents and the backlog. The old plan step "publish the corpus data release first; the fetch runs at image build time" belonged to the unmerged corpus branch and is **dropped**.

Under the local-only decision, the corpus branch `wave-pwmcp-20261027` (`ac15a7df1`) must change before it can land. **It is not needed for r4.**

- `tools/corpus/console_corpus.py` and `tests/corpus/console/CORPUS-ASSETS.json` must change from fetch-from-release-asset to verify-a-local-directory. Keep the sha256 pin and the deterministic tar. The source would be an explicit local path, for example `PWMCP_CONSOLE_CORPUS_DIR`, with no download URL.
- The two real-corpus tests must skip explicitly with their reason when the directory is absent. `PWMCP_REQUIRE_CONSOLE_CORPUS=1` turns absence into a failure, which is the operator's local opt-in. The gate lanes stay corpus-free, so tester-unified needs no bake. Drop `pwmcp/tester-unified-console-corpus.patch.txt` from the plan.
- Drop the branch's `1.64.0-r4` coordinate bump (`ciu.defaults.toml.j2`, `docker-bake.hcl`, `pwmcp.contract.json`); the resolver owns those files. Also drop its "PWMCP-12" entry, whose ID collides with main's different PWMCP-12..17.
- `DESIGN-OCR-PHASE2-3.md` and the README must describe the corpus as local and never published.

### 2b. No unmerged branch has to land before r4

| Branch | Touches | r4 relevance |
|---|---|---|
| `integration-fonts-speed` (`f851cf741`, 8 commits) | `pwmcp/tools/glyphgrid/*` (atlases, `NOTICE.md`, bench), `tests/test_glyphgrid_*` | Tooling only. Not in either image or the bundle, and GlyphGrid stays 501 in the service. Land per the Q2 decision (atlases plus NOTICE) **after** r4, with its own review and pwmcp `r1`. It can go first only if it is already reviewed and green; it must not land between the prep commit and the release. |
| `worktree-agent-ab4156ad5c7c6cb52` (imagegen, 3 commits) | `pwmcp/tools/glyphgrid/imagegen/*`, `tests/test_imagegen.py` | Tooling only. Same rule: after r4. |
| `wave-pwmcp-20261027` (corpus, 4 commits) | see 2a | Rework as in 2a. Not for r4. |
| `backlog-pwmcp-rg-20261030`, `pwmcp-dirty-tree-20261030`, `r2-portfolio-pwmcp-retry{,2}-20261030` | — | Already contained in `main` (0 commits ahead). Delete per the worktree-sprawl rule. |

### 2c. GHCR package `pwmcp-ocr` (new)

- The first `bake --push` with the release PAT creates the package. The PAT needs `write:packages`, the same scope that pushes `pwmcp`.
- The image carries `org.opencontainers.image.source=https://github.com/volkb79-2/vbpub`, which links the package to the repository.
- `build-push.py` then mirrors the repository's visibility onto both packages.
- If the brand-new package cannot be inspected yet (cmru KI-97: API lag or token scope), the release does not fail. It logs `NOT synced: pwmcp-ocr ...` with a one-time UI remediation.
- **No operator decision is needed.** The package follows the repository's visibility, like `pwmcp`. An operator action is needed only if that `NOT synced` line appears: in the GHCR UI, set `pwmcp-ocr` visibility to match `pwmcp` and confirm the repository link.
- Until then, only consumers that enable `pwmcp.ocr.enabled` are affected. It is off by default, so `/srv/pwmcp-instance` does not pull the image.

### 2d. Pre-release documentation statements that become false at release

These must be rewritten in the prep commit, forward-oriented, naming `1.64.0-r4`:

- `pwmcp/README.md:484-490`: "the current published release (`1.64.0-r3`) shipped WITHOUT the OCR image … build it locally", and the `docker build … pwmcp-ocr:1.64.0-r3` example. Keep the local-build recipe only as the dev path.
- `pwmcp/README.md:501`: "… not verified until an image is built and a release is cut". Replace it with what is verified after the post-release smoke in section 4.
- `pwmcp/README.md:602`: "(The OCR image exists from the NEXT release after `1.64.0-r3`.)"
- `pwmcp/docs/DEPLOYMENT.md:204`: "published from the first release made after this wiring landed (`1.64.0-r3` has none)".
- `pwmcp/ciu.defaults.toml.j2:177`: the trailing comment on `[pwmcp.ocr.image] tag`. The resolver rewrites the value but not the comment.
- `pwmcp/CHANGES.md` `[Unreleased]` OCR entry:
  - "The OCR image exists from the NEXT release after `1.64.0-r3`; until then enabling `pwmcp.ocr` needs a local build (README)."
  - "No release was cut and nothing was built or run against real Tesseract."
  - Both become the release body, so reword them as release facts.

### 2e. The shared main checkout has another session's untracked work

`pwmcp/tools/console-capture/{WEB-CONSOLES.md,truenas_rpc.py,truenas_vm_console_capture.py,web_console_session.py}` are untracked in `<checkout>`. Do not delete or commit them. The CMRU release runs in an isolated candidate, so they should not matter. If `cmru release pwmcp --dry-run` refuses because of them, stop and ask their owner instead of removing them.

## 3. Gate lanes

- **pwmcp `r1` (mandatory).** CMRU runs it itself (`[steps.run-tests]`: `run-gate r1`). Run it once more on the prep branch before merging, so a red gate is caught before a release candidate exists. Read the verdict in a separate step, under `flock <controller-program-dir>/gate.lock`, with one gate container at a time.
- **R2: waived** (operator decision). The full native R2 on the changed files covers 384 candidates; 56 are judged and the state is kept resumable. Rerun it later on the btrfs runner with `--jobs`. Five hardening rows are marked "Pending R2", and PWMCP-18 (`ocr_service.py:387`) stays an open survivor. CMRU's pwmcp release does not invoke R2, so no skip flag is involved.
- **tester-unified: no rebuild needed.**
  - r1 passed on 2026-11-02 against the current image with `PWMCP_REQUIRE_TEST_DEPS=1` (`logs/pwmcp-harden-r1b.log`), so the image already has the client `test` extra, node and sh.
  - The real-Tesseract test skips by design unless `PWMCP_REQUIRE_TESSERACT=1`.
  - Without the corpus, nothing new is needed in the image.
- **Tools:** run `which -a cmru run-gate assay` and `pip show cmru`, so a clobbered or stale host install is not trusted (memory: devcontainer tool installs get clobbered).

## 4. Post-release

1. **Verify the publication (read-only).**
   - `git ls-remote --tags origin pwmcp-v1.64.0-r4` names the release commit.
   - The GitHub release carries `pwmcp-v1.64.0-r4.tar.xz`.
   - `docker manifest inspect` succeeds for `ghcr.io/volkb79-2/pwmcp:1.64.0-r4` and for `ghcr.io/volkb79-2/pwmcp-ocr:1.64.0-r4`. For `pwmcp-ocr`, check anonymously, after `docker logout ghcr.io` in a throwaway `DOCKER_CONFIG`.
   - The release log shows `Synced pwmcp-ocr visibility to …`. Otherwise do the one-time UI step in 2c.
2. **Smoke the OCR image without the corpus.**
   - Run it as a standalone container on a loopback port: `docker run --rm -d --name pwmcp-ocr-r4-smoke -p <host-address>:18941:8941 ghcr.io/volkb79-2/pwmcp-ocr:1.64.0-r4`.
   - Check that `GET /health` and `GET /v1/engines` return 200 (tesseract available, glyphgrid 501).
   - `POST /v1/ocr` a PNG generated on the spot with Pillow (text such as `HELLO 123`) and check the text and confidence come back.
   - Remove the container **by its exact name**.
   - Record the result, then fix README:501 in a follow-up docs commit if the smoke contradicts it.
3. **Update `/srv/pwmcp-instance`** (currently `state.json` tag `pwmcp-v1.64.0-r3`).
   - Run `XDG_DATA_HOME=/srv python3 <published get.py> update --scope user`, using the `get.py` from the r4 tag or release.
   - Then run `ciu up` from `/srv/pwmcp-instance`.
   - Check that `state.json` names `pwmcp-v1.64.0-r4` and that `ciu health` and `/contract` report the r4 image.
   - The sparse override `/srv/pwmcp-instance/ciu.toml.j2` must not pin `image.tag` or `[pwmcp.ocr.image] tag`. Today it pins neither; recheck after the update.
   - Keep `pwmcp.ocr.enabled` off on this instance unless the operator asks.
   - Load rule: no gate container running at the same time.
4. **Downstream.** mdt-cockpit re-pins the pwmcp `get.py` and version afterwards (release-wave order: mdt-cockpit last).
5. **Backlog and notes.**
   - Close PWMCP-11 phase 1 "first release with the OCR image".
   - Note the R2 waiver and the rerun as a follow-up (R2 pending on the btrfs runner).
   - Update the release-wave memory or handoff with the current state and the next steps only.
   - Delete the merged pwmcp branches listed in 2b.

## 5. Step list for the controller

1. Preflight: tool versions (section 3), `git status` on the shared main checkout (leave the untracked console-capture files alone), and no running gate.
2. `ciu worktree create pwmcp-r4-prep --base "$(git rev-parse main)"`, or a plain `git worktree add` on branch `pwmcp-r4-prep` from `main`.
3. In `pwmcp/` of that worktree, run `python3 scripts/resolve-playwright-version.py --refresh --age-window-days 14`.
   - Expect `PWMCP_VERSION=1.64.0-r4`. If it prints anything else (for example after 2026-11-05: `1.64.1-r1`), stop and report the actual coordinate. The coordinator then uses that coordinate; no decision is needed.
   - Review the diff. Only these generated files may change: `ciu.defaults.toml.j2`, `ciu.toml.j2`, `docker-bake.hcl`, `containers/pwmcp/Dockerfile`, `containers/ocr/Dockerfile`, `pwmcp.contract.json`, `client/requirements-runtime.txt`, `client/pyproject.toml`. `cmru.vars` is ignored.
4. Make the documentation edits from 2d in the same branch.
5. Run `python3 scripts/resolve-playwright-version.py --check`. It must pass.
6. Run the pwmcp `r1` gate on the branch (section 3) and read the verdict separately.
7. Get an independent review of the prep diff (generated files plus docs).
8. Merge to `main` (`git commit --only`, shared-main rule) and push.
9. From `main`, run `cmru release pwmcp --dry-run`. Check:
   - the version is `pwmcp-v1.64.0-r4`;
   - the body is exactly the curated `[Unreleased]` (Added: OCR, user agent; Testing: hardening);
   - nothing refuses.
10. Run `cmru release pwmcp`. The gate is r1, then build both images, push both, the bundle and `get.py`, and the tag. Monitor it, and read the final verdict separately.
11. Post-release steps 1-5 from section 4.

## 6. Operator decisions

**None are required for r4.**

There is one conditional operator action: the one-time GHCR UI visibility step for the new `pwmcp-ocr` package, only if the release logs `NOT synced: pwmcp-ocr` (2c).

Already settled, and applied above:

- the corpus stays local, with the rework in 2a;
- R2 is waived;
- the Q2 decision lands the fonts-speed atlases plus NOTICE after r4;
- the Playwright window is the estate's 14-day freshness rule, which yields `1.64.0-r4` until 2026-11-05.
