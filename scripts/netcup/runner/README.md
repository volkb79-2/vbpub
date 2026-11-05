# Runner host scripts

Generic, reviewed scripts that turn a freshly installed Debian host (see `install-host.py`) into a parallel test
runner: a `runner` account, a Btrfs CoW area, detached-job and checkout-sync helpers, the gate tools, the
`tester-base`/`tester-unified` images and a cockpit container that drives Run-Gate lanes. They hold no host-specific
value: everything that names one host (FQDN, server id, uid, device, paths, pinned versions) comes from
`runner.env`, which is gitignored. Copy `runner.env.example` to `runner.env`, fill it in, and upload the whole
directory (scripts, `lib.sh`, `run-job`, `sync-from-devcontainer`, `runner.env`) to the host together.

Every script sources `lib.sh`, which loads `runner.env` (or `$RUNNER_ENV`) and defines `identity_check`: the host must
answer to the configured FQDN, static hostname and `/etc/vbpub/server-id` before anything is changed. Verify the
provider-side identity (API id, name, hostname, address, state) from the controller before each remote write; the
scripts can only check what the host itself reports.

| Script | Runs as | What it does |
| --- | --- | --- |
| `provision-runner.sh` | root, detached | writes `/etc/vbpub/server-id`, creates the runner account (uid `RUNNER_UID`, default 1003), formats and mounts the Btrfs CoW partition, installs `run-job` and `sync-from-devcontainer` and the runner scripts with `runner.env` into `~/runner-scripts` (the path the `run-job ... -- bash ~/runner-scripts/<script>` invocations use), creates the tools venv and `uv` |
| `sync-from-devcontainer` | runner | imports a controller-made Git bundle into `~/checkouts/<commit>` and points `~/src/vbpub-current` at it |
| `run-job` | runner | runs a named command detached with `~/jobs/<name>/job.log` and an `EXIT` marker |
| `setup-assay-venvs.sh`, `setup-gate-tools.sh` | runner, via `run-job` | Assay venvs (3.13, 3.14) and the tools venv (cmru, cli-extended, the published Run-Gate wheel) from the synced checkout |
| `build-tester-base.sh`, `build-tester-unified.sh` | runner, via `run-job` | build the two images inside the gates slice |
| `start-cockpit-runner.sh` | runner | creates the cockpit container (docker-run form of the host-folder devcontainer) and remaps its `vscode` user to the runner uid |
| `run-lane.sh` | runner, via `run-job` | runs one registered Run-Gate lane from a runner checkout inside the cockpit |
| `remote-gate.py` | controller | `remote-gate run <host\|auto> <worktree-of-any-git-repo> <lane>`: ships a committed worktree (vbpub, dstdns, any repo) to a runner host and runs one lane there (see CONSUMERS.md, "Remote gate"); host table in a gitignored JSON (`remote-gate-hosts.example.json`) |
| `setup-cockpit-run-gate.sh` | runner, via `run-job` | installs one pinned Run-Gate wheel into its own cockpit venv `~/.venvs/gate-tools<version>` (for repos that need a newer Run-Gate than the cockpit default, e.g. dstdns >= 25.0.1 through `remote-gate.py`) |
| `run-r2-campaign.sh` | runner, via `run-job` | supervises a native Assay R2 campaign across budget exits (`exit 4`), with memory/PSI samples; clamps `--jobs` to the lane's `judge.mutation.jobs_max` (DST-37) and writes `<campaign>/verdict.json` (attempts, final exit, PASS/NOT_PASS) |
| `run-cowtree-assay-smokes.sh` | runner, via `run-job` | cowtree and Assay workspace tests against the real CoW root |
| `setup-cockpit-tools.sh` | runner | installs the pinned Run-Gate wheel and the checkout's Assay into the cockpit user's `~/.venvs/gate-tools` (run once after `start-cockpit-runner.sh`; `run-lane.sh` needs it) |
| `run-jobs-window.sh`, `run-jobs-compare.sh` | runner, via `run-job` | native-R2 `--jobs 1/3/4/6/auto` comparison on fresh state: time-boxed throughput, or full wall time |

## One uid for everything

The `tester-unified` lane user is uid 1003. The runner account (which owns the checkouts and the CoW root) and the
cockpit user (which invokes `run-gate`) must be the same uid, otherwise the `worktree` lane fails as `dubious
ownership`, `Permission denied` under `.run-gate`, or `MDT_COW_ROOT must be owned by the non-root caller uid`
(Run-Gate RG-150, backlog NC-04, mdt-cockpit MDT-052). `provision-runner.sh` creates the account with `RUNNER_UID` and
refuses a different existing uid unless `RUNNER_UID_ALLOW_CHANGE=1`; `start-cockpit-runner.sh` aligns the cockpit user.

## Order

1. Controller: install the host, pin its SSH host key, verify identity, upload the directory.
2. `provision-runner.sh` as a detached root job (`EXIT` marker under `/root/jobs/runner-provision/`).
3. Controller: bundle the reviewed source (`git bundle create ... refs/heads/<branch> refs/tags/assay-v*`), upload to `~/incoming`, run `sync-from-devcontainer`.
4. `setup-assay-venvs.sh`, `setup-gate-tools.sh`, `build-tester-base.sh`, `build-tester-unified.sh` via `run-job`, each only after the previous `EXIT` is 0.
5. mdt host-setup (`install.sh`; run it and the wizard with `PYTHONDONTWRITEBYTECODE=1`, NC-10), the cgprofile observer, then `start-cockpit-runner.sh` and `setup-cockpit-tools.sh`.
6. Lanes and campaigns via `run-lane.sh` / `run-r2-campaign.sh`, one gate at a time unless the host is deliberately overcommitted.

`build-tester-unified.sh` sets `BUILDX_BUILDER=default MDT_BUILDX_PASSTHROUGH=1`: the managed rootless BuildKit cannot see the locally
loaded `tester-base:local` (mdt-cockpit MDT-049).
