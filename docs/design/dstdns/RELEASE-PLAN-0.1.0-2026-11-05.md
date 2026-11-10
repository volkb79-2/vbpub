<!-- origin: 2026-11-05; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/dstdns-release-0.1.0-PLAN.md -->
<!-- relation: dstdns release plan -->

# dstdns 0.1.0 release plan (prepared 2026-11-05, nothing published, tagged or pushed)

Basis: dstdns main 74bb392a (pushed); branch `relgate-fixes8-20261105` (bcb8e691, worktree `/workspaces/dstdns/.worktrees/relgate-fixes8-20261105`) carries DST-180 and the wheelhouse lock and must be merged first. Sources read: `cmru.toml`, `scripts/release/{preflight,gate,env,artifacts,wheelhouse}.py`, `release/*.toml`, plan memo `DSTDNS-DEVELOPMENT-PLAN-2026-11-04.md` (R-REL, Q1-Q5, R1-R6).

## Verdicts on 74bb392a (quoted)

- `scripts/release/gate.py --version 0.1.0` (seeded images, temporary Hub-token config): `[INFO] release gate PASS (evidence: release-evidence/gate.json)`; `lane 'schema' verdict PASS`, `lane 'gate' verdict PASS`, `lane 'release-r1' verdict PASS`, `lane 'release_r3' verdict PASS; exit_code 0`.
- `run-gate fresh-landscape`: `run-gate: lane 'fresh-landscape' verdict PASS; exit_code 0` (24 containers up afterwards).
- Live checks on that stack (test-runner, `RUN_LIVE_TESTS=1`): `tests/integration/test_ddcli_live.py` + `test_webapp_controller_proxy.py` + `test_week2_features.py` + `tests/contract/test_b3_page1_keyset_indexes.py`: 88 passed, 2 failed (`TestWeek2Caching::test_domain_stats_caching`, `::test_web_tech_stats_caching`). Root cause DST-180 (see branch): the stats routes took `Depends(lambda: None)` for their documented Redis cache, and the empty technology-stats body lacked `_cache_hit`. Fixed on `relgate-fixes8-20261105`; unit proof `tests/unit/test_stats_cache_wiring.py`; not re-run live (the stack runs the old image).
- `tests/schema` live tests: the gate's own `schema` lane PASS (includes test_runs_list_pg.py and test_domain_stats_filters_pg.py). The O2 EXPLAIN half has no test (DST-163 open). The e2e UI round trip / perf budget tests (tests/e2e/ui, browser, pwmcp) were not run: the UI browser tests need pwmcp, which is not in the stack profile.
- Ad hoc, with the stack present, `tests/contract` has pre-existing reds that are identical on main (6 routes answer 5xx/503 in `test_get_endpoints_without_path_params_do_not_return_5xx`: classification/parameter-sets, classification/tasks, config-plane/services, landscape/{instances,hosts,containers}; role_matrix cases; live workflow-contract errors). They are not members of the release gate; DST-180 adds none.

## The command

From the main checkout (`/workspaces/dstdns`, branch main == origin/main; cmru releases an isolated origin/main snapshot, so everything must be pushed first):

```
export DOCKER_CONFIG=<temporary authenticated config, see Credentials>   # gate/bake/versions need Hub
unset MDT_COW_ROOT COWTREE_MODE
<controller-home>/cmru-src-venv/bin/cmru release dstdns --set-version 0.1.0
```

`--set-version 0.1.0` is required: it is the first release (dry-run: `dstdns: (first release) -> dstdns-v0.1.0 (set)`), and the planned version is the input of the major guard. No `--major`/`--minor`: gate.py's guard is 0.x-aware (a contract change only needs the MINOR raised on 0.x; with no previous tag the diff base is the root commit). Signing is waived: `manifest_pubkey` is unset, so `sign.py` signs nothing and the push step logs "unsigned release" (no `DSTDNS_MINISIGN_KEY_FILE` needed; `minisign` is still in `required_commands` for the installer and a missing binary only matters on the cockpit that installs).

Dry-run already executed (prints the plan, tags nothing): `cmru release dstdns --set-version 0.1.0 --dry-run` -> `[DRY] Would tag: dstdns-v0.1.0`, `[INFO] [DRY RUN] No tags pushed, nothing built/published.` Release body = `## [0.1.0] - 2026-11-05` generated from the FULL commit history since the repository root (the "Added/Fixed/Changed" sections are empty or merge-commit noise); `DSTDNS_RELEASE_NOTES` in cmru.toml `[env]` is appended. Risk R-notes below.

## What cmru will do, step by step (from `cmru.toml`)

1. `prepare` (quiet): `python3 scripts/release/preflight.py` (review binding, tool floors, waivers) and `cmru versions check dstdns`.
2. `run-tests`: `python3 scripts/release/gate.py` (the gate above; ~45-60 min; brings its own ciu instance up with `ciu up --profile core --profile db --profile runner --deploy --healthcheck`, runs `run-gate release-gate --json`, always `ciu clean --yes`).
3. `build`: env from `scripts/release/env.py` (VERSION/BUILD_VERSION 0.1.0, REGISTRY ghcr.io, RELEASE_NAMESPACE volkb79-2, REVISION, SOURCE_DATE_EPOCH); `cmru handler oci-image-build --bake-target release-registry`; `docker buildx bake ddcli-wheel` -> `dist/wheels/ddcli-0.1.0-py3-none-any.whl`; `artifacts.py verify-images` (labels = tag/commit, non-root, import smoke); `wheelhouse.py stage --out dist/vendor`; `artifacts.py stage-bundle --check-only --wheel-dir dist/vendor` (extracts into an empty dir and runs `ciu check`); `cmru installer render dstdns --output dist/get.py`.
4. `push`: `artifacts.py check-push-plan` (refuses anything but `<REGISTRY>/..:<VERSION>` for exactly the 8 images); `cmru handler oci-image-push`; `artifacts.py bundle` (reads pushed digests -> `images.json`, release pins, `manifest.json`, deterministic `dstdns-v0.1.0.tar.xz`, `ciu check` again, sign only if a pubkey is pinned); `cmru handler tarball-publish` (GitHub Release `dstdns-v0.1.0` in `volkb79-2/dstdns` with `dstdns-v0.1.0.tar.xz` (+ `.sha256`), `dist/get.py`, `dist/images.json`).
5. cmru tags `dstdns-v0.1.0` on the snapshot commit and pushes the tag (this is the first publishing act; before it, steps 1-3 build and gate only).

## Exactly what gets built and pushed (version 0.1.0)

Images (private GHCR, owner from `DSTDNS_RELEASE_OWNER`, default `volkb79-2`):
`ghcr.io/volkb79-2/dstdns-controller:0.1.0`, `dstdns-worker-io`, `dstdns-worker-db`, `dstdns-webapp-server`, `dstdns-webapp-ui-react`, `dstdns-docker-stats-exporter` (the six product images), `dstdns-test-runner` and `dstdns-seeded-targets` (the two test images; `release-test-images`). Digests are read after the push into `images.json`.
Not pushed: `dstdns-app-base:latest` (local base tag; plan risk R1 says check it does not leak: `check-push-plan` enforces exactly 8 refs), `test-runner-ui`, `admin-debug`, `ddcli` image.
Release assets on GitHub (not GHCR): `dstdns-v0.1.0.tar.xz` (bundle: 112 files staged in the dry stage, ddcli wheel + 16-wheel offline wheelhouse + release pins + manifest.json, unsigned), `get.py`, `images.json`.

## Non-publishing preflights executed, and results

| Check | Result |
|---|---|
| `cmru release dstdns --set-version 0.1.0 --dry-run` | OK, plan above |
| `scripts/release/artifacts.py stage-bundle --check-only` | `112 files staged, ciu check passed from an empty directory` |
| `scripts/release/wheelhouse.py stage` with the committed lock | FAILS: `release/wheelhouse.lock does not lock ['ciu', 'cli-extended']` (the lock was never generated; header says NOT YET GENERATED) -> would break the `build` step |
| `docker buildx bake ddcli-wheel` (BUILD_VERSION=0.1.0) | OK: `dist/wheels/ddcli-0.1.0-py3-none-any.whl` (dist is gitignored) |
| `wheelhouse.py lock --lock <scratch> --ddcli-wheel-dir dist/wheels` then `stage --lock <scratch>` | OK: 16 wheels locked (ciu 7.23.0, cli-extended 0.5.2, Jinja2 3.1.6, PyYAML 6.0.3, click 8.5.0, rich 15.0.0, requests 2.34.2, urllib3 2.8.0, certifi 2026.10.19, ...), 16 staged, matches the 16 rows in cmru.toml. The generated file is committed on `relgate-fixes8-20261105` (bcb8e691) as `release/wheelhouse.lock` for review. |
| `python3 scripts/release/preflight.py` | REFUSES: `review: no release review record nyxloom-trove/reviews/release/root..<sha>.md` (see step list) |
| `cmru versions check dstdns` | REFUSES without `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` in the environment (5+ OCI targets use them; 105 version targets in cmru.toml). Not run for real: it spends the shared Hub PAT budget. |
| `scripts/release/env.py` | cannot run before the tag exists (it verifies the local tag peels to the commit); cmru creates the tag inside its release transaction |
| `release/rigor-waivers.toml` | one waiver left: `worker_io_dns_operations` (WORKER-IO-DNS-COVERAGE, expires_before 0.3.0); the lane now PASSES (DST-120 fix) so the waiver is stale and should be deleted (gate.py reports used waivers; an unused one is harmless but misleading) |

## Step list for the controller (in order)

1. Merge `relgate-fixes8-20261105` (DST-180 + wheelhouse lock); review the lock diff (versions all >= 14 days old per the tool; ciu 7.23.0 / cli-extended 0.5.2 come from vbpub releases). Push main.
2. Write the independent release review record `nyxloom-trove/reviews/release/root..<40-hex of the candidate or the reviewed commit>.md` with header `Verdict: ACCEPT`, a reviewer name that is not an author/committer in the range, and `Reviewed-commit: <40 hex>`; only review records and CHANGES.md may change after it (preflight rejects anything else). This is the human/independent gate; I did not fabricate one.
3. Remove the stale `worker_io_dns_operations` waiver (optional but recommended; part of the same pre-review commit so it is covered).
4. Seed the Hub images and use a temporary authenticated config (Credentials) for the whole `cmru release` run; make sure nothing else uses the PAT (200/h shared). Estimated Hub cost: `versions check` 105 targets worst case + bake HEADs + compose `pull_policy: always` pulls; the first `release` consumed the whole 100/h anonymous window in earlier attempts.
5. `cmru release dstdns --set-version 0.1.0 --dry-run` once more on the final main, then the real run (above). Keep the worktree it prints for `--resume` (never `--resume` without `--set-version`, nyxloom memory).
6. After it: verify `gh release view dstdns-v0.1.0`, `sha256sum -c`, pull the 8 digests from images.json, install on r1002 (Q3: r1002 qualification; v1001 rehearsal gates promotion, not the tag).

## Credentials

- GHCR push: `gh` login on this cockpit (token scopes: `delete:packages`, `repo`, `workflow`, `write:packages`; account volkb79-2) is what cmru uses (`token_source = "gh"` in vbpub; dstdns has no `cmru.secret.toml`; `--secret-file` is only for a root overlay). The dry-run already authenticated to GitHub for the fetch/plan. cmru's oci handlers log in to `ghcr.io` with it (`env.py` documents this).
- Docker Hub (read): PAT in `<controller-home>/.config/dockerhub/credentials.env` (keys `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`; never print). For `cmru versions check` export both from that file; for bake/up use a temporary `DOCKER_CONFIG` (`d=$(mktemp -d); chmod 700 $d; (set -a; . credentials.env; printf %s "$DOCKERHUB_TOKEN" | DOCKER_CONFIG=$d docker login -u "$DOCKERHUB_USERNAME" --password-stdin)`, `rm -rf $d` after).
- Seeded images (no Hub quota): `for r in python:3.14-slim-trixie python:3.14-slim busybox:1.38 node:26.12.1-trixie-slim; do docker pull -q mirror.gcr.io/library/$r; done` then `docker tag mirror.gcr.io/library/<r> <r>` for the python and busybox ones (node's local tag already exists; its digest `sha256:94816674...` matches the pin in docker-bake.hcl). alpine:3.24.2 and the other core/db/runner images were already local. Compose services declared `pull_policy: always` (consul, vault, init containers) still query Hub; ciu has no flag to turn that off (DST-153 open).
- Signing: waived for 0.1.0.

## Risks

- R-lock (blocking): `release/wheelhouse.lock` ungenerated on main -> `build` fails. Fixed on the branch; must merge.
- R-review (blocking): `preflight.py` needs the release review record; nothing in the repo satisfies it.
- R-hub: Hub 429 at bake, `versions check` and compose pulls; PAT shared with other releases (200/h). Run `cmru release` only when no other consumer is active; consider the DST-153 mirror step first.
- R-gate-time: `gate.py` inside `cmru release` takes ~45-60 min and needs ~4.9 GiB RAM peak plus the stack; memory PSI must stay below 25 (gate.py waits); one stack at a time on the controller.
- R-teardown: `ciu clean` exits 1 in this devcontainer (CIU-164/179): the root-helper cannot remove `vol-*` dirs, so gate.py's `finally` reports `clean_exit: 1` and leaves `vol-*` directories and sometimes containers (observed). The gate result is unaffected, but the NEXT run (or `ciu up`) fails on stale `vol-*` dirs unless each is removed entirely via a root busybox container by exact path. Do that between any two runs. cmru's isolated worktree is a different checkout/instance id, so its stale volumes sit under the worktree path.
- R-notes: the GitHub release body is the whole history since the root commit in merge-commit form; consider a curated `CHANGES.md` section for 0.1.0 before the cut (preflight allows a CHANGES.md edit after the review record).
- R-signing/trust: unsigned bundle (accepted for staging); `get.py` verifies sha256 and manifest only. Signing becomes mandatory before the first production deploy (DST-38).
- R-redis: redis >=7.4 first-import is order-fragile under pytest-cov with a dotted package source (DST-141); release lanes are already path-scoped, any new lane must follow.
- R-tests-not-green-live: DST-162 style leftovers: `tests/integration/test_ddcli_live.py` was 88 passed / 2 failed before DST-180; `test_get_endpoints_without_path_params_do_not_return_5xx` is red on main with a live stack (6 routes), not part of the gate.
- R-v1001/R2: CIU-175 (non-thin remote deploy copies secrets) must stay unused until ciu 7.21.1+ (the lock already pins ciu 7.23.0 for the bundle).

## Current state (updated 2026-11-05, relgate-final run on main b0d031c7)

Verdicts on b0d031c7 (includes DST-180 and `release/wheelhouse.lock`; seeded images + temporary Hub config, removed afterwards):
- `scripts/release/gate.py --commit b0d031c7... --version 0.1.0`: `[INFO] release gate PASS (evidence: release-evidence/gate.json)`; schema, gate, release-r1 (incl. worker_io_dns_operations), release_r3 all PASS; teardown clean (no vol-* dirs, no containers left).
- `RUN_LIVE_TESTS=1 run-gate fresh-landscape`: `lane 'fresh-landscape' verdict PASS; exit_code 0` (29 passed; images rebuilt from b0d031c7).
- Live checks on that stack: test_ddcli_live + test_webapp_controller_proxy + test_week2_features + test_b3_page1_keyset_indexes: 90 passed, 0 failed, 0 skipped. `TestWeek2Caching::test_domain_stats_caching` and `::test_web_tech_stats_caching` (in tests/integration/test_ddcli_live.py) now PASS (were the 2 failures before DST-180).
- Waiver removal: branch `relgate-final-20261105` commit d5042be0 (worktree `.worktrees/relgate-final-20261105`, on top of b0d031c7): `release/rigor-waivers.toml` has no waivers; load_waivers -> []; release gate/preflight/common/sequence/wiring tests 199 passed. NOT merged. Note: it changes a gate config file, so merge it BEFORE the review record and rerun at most the cheap preflight; the gate verdict above was measured without it (the waiver was unused, so no behavioral difference).

Blocker list now (supersedes the Risks marked fixed):
1. [open] Independent release review record `nyxloom-trove/reviews/release/root..<sha>.md` (R-review); must cover the final commit incl. the waiver removal.
2. [open] Operator confirmation to run the real `cmru release dstdns --set-version 0.1.0`.
3. [open] Merge `relgate-final-20261105` (d5042be0) and push main (cmru releases origin/main).
4. [fixed] R-lock: wheelhouse.lock is on main (b0d031c7).
5. [fixed] DST-180 stats cache live failures.
6. [still true, not blocking] R-hub (use seeded images + temp config; PAT shared), R-teardown (CIU-164: this run tore down cleanly, but still check vol-* before each run), DST-162/163 open, test_get_endpoints_without_path_params 5xx reds outside the gate, unsigned bundle (DST-38), release notes noise (R-notes).
