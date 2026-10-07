# MM-MOVE + NYX-CLIX report

Branch `mm-move` (worktree `/workspaces/vbpub/.worktrees/mm-move`), base `main` at `2b94cb433`.
Not merged, not pushed. Everything below was run unless marked INFERENCE or NOT RUN.

Commits (oldest first): `4fa965773` move; `95f97f4b6` references + mattermost ciu root; `65210f20c`
mattermost lane; `c6557bb79` nyxloom wheel dependency; `a6500c523` program/backlog docs; the report commit
on top is the branch tip.

## Part 2: MM-MOVE

### What moved
`git mv nyxloom/mattermost mattermost` (7 tracked files), plus `nyxloom/tests/test_mattermost_reachability.py`
and `test_mattermost_provision_hook.py` to `mattermost/tests/`. The ignored `vol-*` and `.ciu/` stay in the main
checkout (controller's job, see "Live cutover").

### Finding that changes the cutover: mattermost was a SUB-STACK of nyxloom's ciu root
There is no `mattermost/ciu.toml` (ciu.toml is rendered and gitignored). The stack inherited `deploy.*`,
`[governance]` and `[ciu]` from `nyxloom/ciu.global.defaults.toml.j2` and was listed in that root's
`[deploy.profiles.default] stacks`. Moving the directory alone would leave it with no ciu root at all.
So the stack is now its own standalone root (the pwmcp layout: stack files in the root dir), via a new
`mattermost/ciu.global.defaults.toml.j2` (`project_name = "mattermost"`, `environment_tag = "$INSTANCE_ID"`,
`network_name`, hostdir env, governance copied from nyxloom's root, `standalone_root = true`,
`require_certs/fqdn = false`, `auto_connect_network = false`, same as the old inherited values). nyxloom's root
profile now lists `["nyxloomd"]` only. The `container_prefix` template fallback `nyxloom` became `mattermost`.
The root declares NO profile, so a bare `ciu up` selects nothing: use `ciu up --dir mattermost -y` (as the
README does). I tried a `stacks = ["."]` profile in a scratch copy: ciu resolved zero identities, so none was
committed.

### Reference table (`git grep -E 'nyxloom/mattermost|mattermost/'` plus relative forms)

Updated:

| File | What | Why |
|---|---|---|
| `nyxloom/cmru.toml` | removed `oci.postgres` and `oci.mattermost` version targets (comment left); release-notes install snippet (Part 1) | both targets track pins that now live in `mattermost/ciu.defaults.toml.j2`; re-declare in mattermost's own cmru.toml in the later package |
| `nyxloom/nyxloom-trove/nyxloom.toml` | 3 secret paths to `mattermost/.ciu/secrets/...`; `[intake_bridge] container`/`base_url` commented out with NL-38 pointer | volatile names, no new literals |
| `nyxloom/ciu.global.defaults.toml.j2` | `stacks = ["nyxloomd"]`, comment | stack left this root |
| `nyxloom/src/nyxloom/decision_chat.py` | docstring path | text only |
| `nyxloom/src/nyxloom/intake_bridge.py` | NOT changed | its `mattermost/hooks/...` and `mattermost/README.md` mentions are repo-root-relative and now correct |
| `nyxloom/docs/ARCHITECTURE.md` | stack path | living doc |
| `nyxloom/ntfy/README.md` | `../mattermost/` to `../../mattermost/` | relative link |
| `scripts/debian-install-v2/debian_install_v2/notify.py`, `README.md`, `TODO.md` | CONSUMER.md path | consumer doc/docstring |
| `scripts/netcup/README.md` (201, 214), `DESIGN-GUIDE.md` | CONSUMER.md link, secret path `../../mattermost/.ciu/secrets/installer_webhook_url` | consumer contract |
| `docs/CONSUMERS.md:157` | secret path | consumer contract |
| `mattermost/README.md` | `--dir`, secret paths, `--root-folder /workspaces/vbpub/mattermost` (follow-up: was wrongly `--define-root`), project-store secret paths `mattermost/.ciu/secrets/mattermost/<name>` (INFERENCE, see below), every `nyxloom-1dd3d1-mattermost` literal replaced by `<container_prefix>-mattermost`, new "Location" paragraph, labels.prefix note | runbook |
| `mattermost/CONSUMER.md`, `ciu.defaults.toml.j2`, `ciu.compose.yml.j2` (comment `../nyxloom/ntfy/server.yml`) | paths and comments | text only |
| `mattermost/docker-compose.yml` | the absolute `/home/vb/volkb79-2/vbpub/nyxloom/mattermost/vol-*` bind paths (functional) and the postgres secret path; header comments | the plain-compose fallback binds absolute paths |
| `mattermost/tests/*.py` | `parents[1] / "mattermost" / "hooks"` to `parents[1] / "hooks"` (same for tools), docstrings | the files moved one level |
| `libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md`, `nyxloom/.../NL-30-*.md` | Part 1 rows | adoption tracking |
| `nyxloom/.../NL-38-*.md` | dated "Update 2026-10-07" section | the stack moved; resolve must target the mattermost root |

Left as is (reason):

| File | Reason |
|---|---|
| `nyxloom/CHANGES.md` (11 hits), `nyxloom/nyxloom-trove/reports/nyxloom-P106..P110-REPORT.md`, `backlog/NL-17-*.md` | dated history |
| `ciu/CHANGES.md:374`, `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` (11), `ciu/docs/SPEC-V8.md:1162`, `cmru/KNOWN_ISSUES_TODO_BACKLOG.md:1746` | dated findings/history in other tools' backlogs, describe the pre-move location |
| `nyxloom/tests/test_intake_bridge.py`, `nyxloom/src/nyxloom/config.py:172` (`nyxloom-prod-mattermost`) | synthetic fixture/doc literal, not an instance name |
| `mattermost/tests/test_mattermost_provision_hook.py:420` (`nyxloom-prod`) | synthetic fixture |
| `mattermost/docker-compose.yml` `container_name: nyxloom-mattermost[-db]`, project `-p nyxloom-mattermost` | the plain-compose fallback's own FIXED names, distinct from ciu-derived ones; not volatile |
| `mattermost/ciu.compose.yml.j2:352-354` traefik router/service name `nyxloom-mattermost` | a label identifier, not a container name; renaming is a live routing change nobody asked for |
| `nyxloom/ciu.global.defaults.toml.j2:16`, `mattermost/ciu.global.defaults.toml.j2:4` | explanatory comments |
| `ciu/test-repo` etc. | no hits |

`nyxloom/nyxloomd/` (compose, `ciu.toml`, `ciu.defaults`): no reference to Mattermost's network or path. Nothing changed there for MM-MOVE (its Dockerfile changed for NYX-CLIX, below). Other `ciu.toml.j2`-style dependents (netcup, debian-install-v2) read the webhook URL secret file by path only.

### Derived names (INFERENCE until the controller runs ciu)
From `ciu/src/ciu/engine.py:948` (`compose_project_name` = `{project_name}-{environment_tag}-{stack basename}`) and
`mattermost/ciu.compose.yml.j2:18,70` (`container_name = {container_prefix}-mattermost[-db]`, with
`container_prefix = {deploy.project_name}-{deploy.environment_tag}`), where `<ID>` = this checkout's
`INSTANCE_ID` from `mattermost/ciu.instance.generated.toml` (generated, unknown until the controller runs it;
it is NOT the nyxloom id):

- compose project: `mattermost-<ID>-mattermost`
- containers: `mattermost-<ID>-mattermost`, `mattermost-<ID>-mattermost-db`
- private bridge: `mattermost-<ID>-mattermost_internal`; instance network `$DOCKER_NETWORK_INTERNAL` (`mattermost-<...>-<ID>-network` form)
- named volumes: `mattermost-<ID>-mattermost_{mattermost-data,mattermost-plugins,mattermost-client-plugins}`
- hostdirs: `mattermost/vol-postgres-data`, `vol-mattermost-config`, `vol-mattermost-logs`
- secrets (per-stack store `<stack>/.ciu/secrets/<name>`, SPEC S4.9): `mattermost/.ciu/secrets/{installer_webhook_url,installer_pat,daemon_webhook_url,intake_webhook_url,intake_pat}`. The project-store passwords that used to be `nyxloom/.ciu/secrets/mattermost/<name>` become `mattermost/.ciu/secrets/mattermost/<name>`: INFERENCE (stack dir == root dir, and the SPEC puts the project store at `<repo-root>/.ciu/secrets/<name>`); verify after the first `ciu up`.

A real render was attempted in a scratch copy (`git archive` of `mattermost/` into the scratchpad) and not
completed: `ciu render` selects no stack for a profile-less root and `ciu resolve --stack .` returns "not in the
current deploy selection". No name above was observed from ciu output.

### Gates
Pressure checked before each run (`/proc/pressure/cpu` some avg10 0.65 to 3.30). Both runs foreground under
`flock .../gate.lock nice -n 19 ionice -c 3`, from the project dir with `--worktree`.

| Command | EXIT | Per-lane verdict line |
|---|---|---|
| `cd mattermost && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/mm-move suite` (at `65210f20c`) | 0 | `run-gate: lane 'suite' verdict PASS; exit_code 0`; 124 passed; coverage TOTAL 83% (hooks 76%, tools 89%), reported only |
| `cd nyxloom && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/mm-move tester-unified` (at `c6557bb79`, the registered release-gate lane per `cmru.toml [steps.run-tests]`) | 0 | `tester-unified: PASS (exit 0)`; verdict artifact `outcome PASS`, R1 `changed_lines`, `fail_under 100`, `require_branch true`, `PYTHONPATH = "src"` |

Commit `a6500c523` (docs only) and the report commit came after the nyxloom run; they touch no code or lane input.

Coverage: the siblings (`scripts/telegram`, `scripts/gstammtisch-guide`) do not enforce 100%, so neither does
this lane; it reports. Honest note: nyxloom's lane measured only `--cov=src/nyxloom`, so the hook and
`mm_reachability.py` were NEVER measured by any gate before; the 83% is the first measurement, not a regression.
The tests ran in nyxloom's lane but judged nothing about that code.

### Plants (both reverted, `git status` clean after)
1. Moved tests: in `mattermost/hooks/post_compose_provision.py` `_incoming_webhooks` stopped stripping the
   `Incoming:` prefix. Local `python3 -m pytest tests` in `mattermost/`: 4 failed, 120 passed
   (`test_incoming_webhooks_strips_the_prefix_and_pairs_name_to_id`, `test_ensure_webhooks_builds_the_internal_url`,
   `..._siteurl_tracks_expose_public`, `..._refuses_a_duplicated_display_name`). Run with plain pytest, not through
   the gate lane. Reverted with `git checkout`.
2. Wheel check: re-added the old `package-dir` + `packages.find` vendoring to `nyxloom/pyproject.toml`.
   `tests/test_packaging_cli_extended.py::test_pyproject_vendors_no_cli_extended_source_in_any_packaging_config`
   failed, and a REAL `cmru handler wheel-build` of the planted tree produced a wheel for which
   `check_wheel_contents` raised `... vendors cli_extended files: ['cli_extended/__init__.py', 'cli_extended/audit.py', 'cli_extended/cli.py']`.
   Reverted with `git checkout`.

## Part 1: NYX-CLIX

`nyxloom/pyproject.toml`: `cli-extended>=0.3.0` added with the reason; `package-dir` entry and `packages.find`
`where`/`include` for `cli_extended` removed (`where=["src"]`, `include=["nyxloom*"]`, comment forbids re-adding).
Floor reason (honest): 0.3.0 is the latest release and the one nyxloom's suite is verified against. The 8 names
nyxloom imports (`ArgumentSpec, CliFailure, CliIdentity, CliRegistry, OptionSpec, PromptCancelled, VerbGroup,
VerbSpec`) also exist at tag `cli-extended-v0.2.0` (checked), but 0.2.0 is untested here, so the floor was not
lowered. nyxloom does not use `register_skills_verbs`, so cmru's CLI-EXT-26 reason does not apply and is not claimed.

### Wheel-content proof (real build)
`CMRU_WHEEL_BUILDER_IMAGE=wheel-builder:local cmru handler wheel-build --cwd nyxloom` (the variable is not in
nyxloom's `cmru.toml`; the orchestration config supplies it; without it the handler refuses with exit 3), at `c6557bb79`:

```
nyxloom-0.10.1.dev17+g65210f20c.d20261007-py3-none-any.whl   137 files
top-level entries: ['nyxloom', 'nyxloom-0.10.1.dev17+g65210f20c.d20261007.dist-info']
cli_extended entries: []
Requires-Dist: cli-extended>=0.3.0   (then PyYAML>=6, jsonschema>=4, structlog<27,>=24, rich>=15.0.0, pygments>=2.21.0, + extras)
check_wheel_contents: OK
```
Install-order check in a scratch venv (nothing touched `~/.venv`): `pip install nyxloom.whl` alone fails
(`Could not find a version that satisfies the requirement cli-extended>=0.3.0`); after
`pip install --no-index --no-deps cli_extended-0.3.0-py3-none-any.whl` it succeeds, `cli_extended` imports from the
venv's site-packages and `nyxloom --version` runs. The cli-extended wheel there was built from monorepo source with
`SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED=0.3.0`.
Wheel and plant artifacts are in the scratchpad (`wheel-proof/`, `wheel-plant/`).

### Other changes
- `nyxloom/tests/test_packaging_cli_extended.py` (new, 12 tests): static oracles (dependency + reason at the
  declaration, no vendoring, no `cli-extended/src` in any gate config, every `PYTHONPATH` in `assay.toml` is `src`)
  and `check_wheel_contents` on synthetic wheels with one defect each. The real-wheel check is run manually as above;
  it is not a gate lane (cmru's `installed-wheel` lane analogue was not built).
- Gate lanes (CX-D1 vs CX-D3): `assay.toml` both lanes now `PYTHONPATH = "src"`. nyxloom is a tool, so CX-D1
  applies (installed wheel); CX-D3 (worktree source on `PYTHONPATH`) is for the Netcup scripts. The lane imports the
  tester-unified image's released cli_extended; the PASS above ran that way. `run-gate.toml` and
  `nyxloom-trove/nyxloom.toml [gates.*]` needed no change (they delegate to the assay lane).
- `nyxloom/nyxloomd/Dockerfile`: this was a REAL break, not just docs. It copied `libraries/cli-extended/src/cli_extended`
  so the vendoring `package-dir` would find it. Now stage 1 builds a cli-extended wheel from the monorepo
  (`COPY libraries/cli-extended/{pyproject.toml,README.md,src}`, `ARG CLI_EXTENDED_VERSION=0.3.0` as the pretend
  version, bump with the floor) and the nyxloom wheel; stage 2 installs the cli-extended wheel first with
  `--no-index --no-deps`, then the nyxloom wheel. Root `.dockerignore` whitelists the library's `pyproject.toml`
  and `README.md`. NOT RUN: the docker image build itself (only its wheel and install-order steps were reproduced
  above). The dev bind-mount of `nyxloom/src` is unaffected. The first image build after merge should be watched.
- CX-D2 / release bundle: nyxloom has no `get.py` or installer bundle (`artifacts = ["wheel"]`), so there is no
  `[[installer.wheels]]` to add. Release-notes install text in `cmru.toml [env] NYXLOOM_RELEASE_NOTES` now says to
  install with `pipx install --preinstall ./cli_extended-*.whl nyxloom-*.whl`. NOT VERIFIED end to end (pipx would
  have written into `~/.local`; only the pip equivalent was run in a scratch venv). Other consumers installing the
  nyxloom wheel (the devcontainer's `~/.venv`) must install the cli-extended wheel first; the live `~/.venv` already
  has cli-extended 0.3.0, and once the OLD nyxloom 0.10.0 wheel (which also owns `cli_extended/*`) is replaced by the
  new one the double ownership ends. Reinstall order: cli-extended first, then nyxloom, so uninstalling nyxloom 0.10.0
  cannot delete cli_extended files out from under the library.
- `nyxloom/CHANGES.md`: added `## [Unreleased]` containing ONLY an HTML comment (the file had no such heading, cmru's
  own CHANGES uses this shape; not exercised against a real `cmru release` here).
- Release-notes line for the next nyxloom release: "nyxloom no longer vendors cli_extended: it declares
  `cli-extended>=0.3.0` as a real dependency (CX-D1). Install the released cli-extended wheel first (GitHub Releases
  only, never PyPI)."
- Docs: `ARCHITECTURE.md` statement corrected; `CLI-REFERENCE.md` audit section marked superseded; program doc row
  3 and NL-30 items 1-3 ticked (CLI-registry items of NL-30 remain open); `NL-30` states the 0.3.0 floor.

## Live cutover: what the controller must do
1. Merge, then in the main checkout confirm `nyxloom/mattermost/` is gone (only ignored `vol-*` and `.ciu/` remain
   there; `git mv` moved the tracked files only). Archive the old `nyxloom/mattermost/vol-*` and `.ciu/` (and the
   project-store passwords at `nyxloom/.ciu/secrets/mattermost/`) out of the way; the operator wants no data migration
   and a clean instance with new users.
2. Stop and remove the OLD stack by exact container name (`nyxloom-<oldid>-mattermost`, `-db`) before bringing up
   the new one: the templates still emit the same Traefik router name `nyxloom-mattermost` and the same public Host
   rule, so two live copies would contend for one route (INFERENCE from the labels, not tested).
3. `ciu up --dir mattermost -y` from `/workspaces/vbpub` (NOT bare `ciu up`; the root declares no profile).
   First run `ciu env generate --root-folder /workspaces/vbpub/mattermost` (ciu 7.15 has no `--define-root`; the
   README's earlier `--define-root` text was wrong and is corrected in the follow-up round). First run
   creates `mattermost/ciu.instance.generated.toml`, `mattermost/.ciu/`, `mattermost/vol-*`; note
   `ciu env generate` creates and attaches the devcontainer to a docker network as a side effect (it did when I ran it
   in a scratch copy; I removed that network, `mattermost-9uqz27-network`, by exact name afterwards).
4. Verify the names above against `docker ps`; correct the INFERENCE items (esp. the project-store secret path).
5. Secrets consumers to re-point after the new instance mints them (all paths are now under `mattermost/.ciu/secrets/`):
   `NYXLOOM_WEBHOOK_URL` (`daemon_webhook_url`), `NYXLOOM_INTAKE_WEBHOOK_URL` (`intake_webhook_url`),
   `NYXLOOM_INTAKE_MM_TOKEN` (`intake_pat`), and netcup / debian-install-v2 reading `installer_webhook_url`
   (`scripts/netcup/README.md`, `docs/CONSUMERS.md`); external producers keep the PUBLIC webhook URL but it will CHANGE
   (new webhook ids), so installers and notifiers need the new URL.
6. `nyxloom.toml [intake_bridge]` `container`/`base_url` are commented out: the intake bridge will not poll until NL-38
   resolution lands or the controller uncomments and fills them for the live instance. NL-38 now notes the resolve
   must target the mattermost root.
7. Re-run `mattermost/tools/mm_reachability.py` against the new instance. The first `mm-move` merge also drops the
   `oci.mattermost`/`oci.postgres` version targets from nyxloom's `cmru.toml`; `cmru versions` for those pins goes
   quiet until mattermost gets its own cmru project (later package).
8. After merge, rebuild the nyxloomd image once (NOT RUN here) and reinstall nyxloom into `~/.venv` cli-extended first.
9. `ciu worktree create` instances of `nyxloom` no longer start Mattermost; `ciu up` in nyxloom root brings only
   nyxloomd.

## Follow-up round (review ACCEPT-conditional)
- B1: `cmru.orchestration.toml` nyxloom `depends_on = ["cli-extended", "cmru"]`. From `nyxloom/`, `cmru status` exit 0
  (`nyxloom-v0.10.0`, next `nyxloom-v0.10.1`) and `cmru dependencies` exit 0 (`nyxloom <- cli-extended, cmru`).
- README: all 8+ `--define-root` uses are `--root-folder` (verified in `ciu up --help`, `ciu down --help`,
  `ciu env generate --help`); the Deploy block documents `ciu env generate --root-folder ...` then `ciu up --dir mattermost -y`.
- Guard: `mattermost/tests/test_stack_paths.py` (8 tests; suite now 132). Plant 1 (compose bind path reverted to
  `nyxloom/mattermost`): 2 failed. Plant 2 (`post_compose` pointed at `../nyxloom/mattermost/hooks/...`): 2 failed.
  Both reverted by `git checkout`.
- Nits: pyproject comment points at `cmru.toml [env] NYXLOOM_RELEASE_NOTES`; stale "bundle" comments fixed in
  `nyxloomd/ciu.compose.yml.j2` and `docker-bake.hcl`; `mattermost` row added to `TESTING-ESTATE-CHECKLIST.md`.
- Ruling: NL-39 filed (released, sha256-verified cli-extended wheel for the nyxloomd image, CX-D2); the Dockerfile has a
  one-line `TODO(NL-39)`; not implemented.

## Limits of this report
- The mattermost lane's coverage is reported, not enforced; no mutation/assay lane was added.
- Nothing was rendered by ciu for the real mattermost root, so the compose and network names are derived, not observed.
- The nyxloomd image build and a pipx install were not run.
