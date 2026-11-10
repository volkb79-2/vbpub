<!-- origin: 2026-11-03; status: decided; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/REQUIREMENTS-RENAMES-2026-11-03.md -->
<!-- relation: final requirements-manifest rename decision; supersedes REQUIREMENTS-NAMING-INVENTORY -->

# Requirements manifest renames — 2026-11-03

Branch: `req-renames-20261103`, based on `main` at `c08b5b89f8a97919b7913dda7259884a8c953145`.
Each tool has one commit with the requested co-author trailer. No push, merge,
or `run-gate` invocation was performed. The worktree is clean.

## Tool changes

| Tool | Manifest and commit | Changed files and referrers | Focused tests and checks | Controller lane |
|---|---|---|---|---|
| Repository root / shared scripts | Split `requirements.txt` into `requirements-runtime.txt` (PyYAML, requests, Telethon) and `requirements-test.txt` (pytest). `54fb700008d932a54180aaf4fbc6ea48e3a50a62` | `README.md` now documents both install commands and the shared-script setup; updated `scripts/telegram/telegram_setup.py`'s missing-Telethon hint, the root-file note in `cmru/nyxloom-trove/reports/cmru-FLOOR-2026-10-REPORT.md`, and the kept-files list in `docs/plan-cleanup.md`. | Container `req-renames-telegram-20261103`: `scripts/telegram/tests/test_setup_and_reader_smoke.py` — **5 passed**. | `scripts/telegram` Run-Gate lane `suite` (controller-owned). |
| `legacy-experiments/voice2text-ai/oobabooga-llm` | Renamed to `requirements-runtime.txt`; pins unchanged. `46393ec70f9ec64812c35ad3b5d100d71c1c9b5f` | Updated `README.md` and `setup.sh`. The Dockerfile's upstream `/app/requirements/full/requirements_cpu_only.txt` install and generated `/tmp/requirements_filtered.txt` remain unchanged. | No tracked test suite. `bash -n legacy-experiments/voice2text-ai/oobabooga-llm/setup.sh` passed. | No registered lane found. |
| `legacy-experiments/voice2text-ai/speech-to-copilot/scanner` | Renamed to `requirements-runtime.txt`; pins unchanged. `b48a58e423d01292aca868f8c8c31ee511a52846` | Updated `scanner/Dockerfile.dev` `COPY` and `pip install` paths. The inventory attributed `speech-to-copilot/README.md:298` to the scanner, but that line describes a separate `api/requirements.txt` placeholder; no matching API manifest is tracked, so it was left untouched. | No tracked test suite. | No registered lane found. |
| `plesk-mailbox-create` | Renamed to `requirements-runtime.txt`; pins unchanged. `b1e053fee2e430a1442e9d26eb158ca153e2827e` | Updated the install command in `README.md`. | No project test suite. | `plesk-mailbox-create` Run-Gate lane `smoke` (controller-owned). |
| `scripts/cgroup-profiler` | Renamed to `requirements-report.txt`; file contents and pins unchanged. `53723783e265b4aa81f49efc30a5a537f73274e1` | Updated `setup.sh`, `Dockerfile`, `.dockerignore`, `pyproject.toml`, `build-push.py`, `README.md`, `DESIGN.md`, `lib/report_html.py`, the test docstring, `CHANGES.md`, CP-32 and its index, four trove reports, and `scripts/LANE-MATRIX-PROGRESS.md`. `setup.sh` and the image build still install the same lock. | Container `req-renames-cgprofile-20261103`: `tests/test_docs.py`, `tests/test_deployment_contract.py`, and `tests/test_build_push.py` — **27 passed**. `bash -n scripts/cgroup-profiler/setup.sh` passed. No existing test directly asserted the old manifest filename. | The CMRU release gate runs `r0-r1` (unit plus coverage) and `r3` (canary rejection); controller-owned. No R2 mutation lane was run. |

The root manifests were checked as a split: their combined, sorted entries
match the original root manifest, with pytest listed only in the test file.
The four simple renames (oobabooga, scanner, plesk, and cgroup-profiler) were
byte-for-byte compared with their original files. Fixed-string `git grep`
checks found no remaining references to the old filename in each renamed
manifest's actual consumer scope. No existing doc test asserted these
filenames directly.

The host-side CMRU slice probe confirmed `dev-gates.slice` is loaded from
`/etc/systemd/system/dev-gates.slice`. Focused tests ran serially in
`tester-unified:local`, detached, under `--cgroup-parent=dev-gates.slice`,
with `DOCKER_CONFIG=<controller-home>/.docker-nocreds` and both the physical and
devcontainer repository paths mounted. The first Telegram launch used only
the cockpit path and could not see the worktree; that container was removed.
The corrected dual-mount invocation passed 5/5.
Afterward, another CMRU coverage gate held the shared gate lock while the main
release was also running. A read-only host-side check for the failed launch's
`/workspaces/vbpub` bind source could not acquire the lock and was stopped
before launching a probe. Docker may have created that host path as an empty
directory; it was not inspected or removed while the lock was held. Check it
through the host-side probe after the current gate releases the lock, and
remove it only if it is confirmed empty and was created by the failed launch.

## Exclusions and evidence

- Left the three `assay/` manifests for assay's next release:
  `assay/gate/distribution/build-requirements.txt`,
  `assay/gate/distribution/lint-requirements.txt`, and
  `assay/nyxloom-trove/carve-assets/P24/build-requirements.txt`.
- Left `game_stuff/empyrion/requirements.txt` unchanged. It is tracked, but a
  fixed-string search found no tracked reference to that path, and the project
  has no tracked `setup.sh`, Dockerfile, or `pip install -r` consumer. The only
  matching install guidance found is `TOOLS-README.md:488`, which directly
  installs `google-api-python-client`; it does not establish that this
  manifest is used.
- Kept the oobabooga Dockerfile's separate upstream dependency path and its
  temporary filtered file unchanged; neither refers to the local manifest.
- Left `pwmcp/client/requirements-runtime.txt` unchanged because it already
  conforms. `scripts/debian-install-v2/requirements-wizard.txt` was already
  present on the base branch. Constraint manifests were outside this task.
