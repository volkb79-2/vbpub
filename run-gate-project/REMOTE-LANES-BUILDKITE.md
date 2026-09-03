# run-gate — remote and asynchronous lanes on Buildkite

Sibling of **`CONSUMERS.md`** (adoption mechanics) and **`LANE-AUTHORING.md`**
(what makes a lane good). This file is the operator's manual for running
lanes on hosts other than the one you are sitting at: enrolling a host as a
Buildkite agent, giving this host access to trigger and collect, and how the
result plugs into run-gate, assay and ciu. Written 2026-09-02; the Buildkite
commands were read from the vendor's docs that day and are quoted as found —
**verify the apt channel once against the live docs before the first
enrollment**, it is the thing most likely to have moved. The REST paths in §4
were re-read from the vendor docs on 2026-09-03 and are what
`tools/buildkite/bk-lane.sh` actually calls.

The two seams that need no remote host — the pipeline generator (§3) and the
trigger/collector (§4) — are now written, and **tested through `--dry-run`
only**: no build has been created and no artifact downloaded by them yet (§6).

The decision this implements is **D-110.4** (dstdns decisions, 2026-08-20):
long and asynchronous lanes are additional `run-gate.toml` lanes with large
budgets, run by Buildkite agents on the remote hosts, calling the same
`./run-gate.py <lane>` every other consumer calls. Nothing in run-gate or
assay changes for step 1; Buildkite is the queue, the trigger, the log store
and the artifact store, and the hosts do the work.

---

## 1. What you gain, and what it costs

| gain | mechanism |
|---|---|
| One gate container per host, enforced across EVERY trigger (agents, humans, the daemon) | a per-host queue plus `concurrency: 1` in a `concurrency_group` |
| Fire-and-forget long lanes with a durable result | the build record, its log, its artifacts |
| Retries, scheduled nightlies, a history you can browse | pipeline features, no code |
| Pipelines that follow the lane list | a first step runs `./run-gate.py --list` and uploads the generated steps |
| The gate container stays offline | only the agent process talks to Buildkite |

| cost | note |
|---|---|
| Outbound HTTPS from each host to `agent.buildkite.com` and to GitHub | the agent polls; nothing inbound is needed |
| A `tester-unified:local` image on each host | build from the checkout (what nyxloom's `tools/remote-mutation-audit-host.sh` does today) or pull a published image, §5 |
| The agent's token on disk, mode 0600 | per-cluster token, revocable in the UI |
| Plan limits of the account | check them before adding the second host |

Alternatives were weighed on 2026-09-02 and stand rejected: **ciu as a remote
job runner** (the v8 spec keeps cross-host work to push/activate and probing
and never drives a host by SSH; a queue, retention, retries and notifications
are the CI system D-110 refused to write), and **plain SSH + systemd-run**
(fine for one synchronous run, becomes that same queue the moment two
triggers exist).

---

## 2. Enrol a host as an agent

Prerequisites on the host: Debian/Ubuntu, Docker Engine with the daemon
running, `git`, outbound HTTPS. Root access is needed once, for the install;
the agent itself runs unprivileged.

**2.1 Install the agent (Debian/Ubuntu, from the vendor docs as read on
2026-09-02).** The docs page for the v3 agent listed the `oldstable` apt
channel that day; if a newer major has since become `stable`, decide which
you want before pasting.

```bash
sudo apt-get update
sudo apt-get install -y apt-transport-https dirmngr curl gpg
curl -fsSL https://keys.openpgp.org/vks/v1/by-fingerprint/32A37959C2FA5C3C99EFBC32A79206696452D198 \
  | sudo gpg --dearmor -o /usr/share/keyrings/buildkite-agent-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/buildkite-agent-archive-keyring.gpg] https://apt.buildkite.com/buildkite-agent oldstable main" \
  | sudo tee /etc/apt/sources.list.d/buildkite-agent.list
sudo apt-get update && sudo apt-get install -y buildkite-agent
```

**2.2 Get an agent token.** In the Buildkite UI: Agents → your cluster →
Agent tokens → create one named for the host. (Accounts without clusters show
the token on the Agents page.) Never commit it; it lives only in the config
file below.

**2.3 Configure.** The file is `/etc/buildkite-agent/buildkite-agent.cfg`.
Set the token, a name, and put the host on its own queue so a step can
target it:

```ini
token="<agent token>"
name="%hostname-%spawn"
queue="gate-<host>"           # or: tags="queue=gate-<host>,docker=true"
tags="docker=true,host=<host>"
build-path="/var/lib/buildkite-agent/builds"
hooks-path="/etc/buildkite-agent/hooks"
spawn=1                       # exactly one job at a time on this host, see §3
```

`spawn=1` is deliberate: the estate's rule is one gate container per host,
and a second agent process on the same host would be a second container.

**2.4 Let the agent use Docker and the estate's slice.**

```bash
sudo usermod -aG docker buildkite-agent
sudo mkdir -p /etc/systemd/system/buildkite-agent.service.d
printf '[Service]\nSlice=dev-background.slice\n' \
  | sudo tee /etc/systemd/system/buildkite-agent.service.d/slice.conf
sudo systemctl daemon-reload
```

`dev-background.slice` is the dev-tier slice `mdt host-setup` governs; if the
host also carries production, add `CPUQuota=` to the same drop-in so a lane
cannot take every core, and keep the shared-host rule from `AGENTS.md` in
mind: this is exactly how the 8-core dev host reached a load of 85.

**2.5 Repository access.** vbpub is public, so `https://github.com/<owner>/vbpub`
clones with no credential. For a private repo (dstdns), give the
`buildkite-agent` user a read-only deploy key in `/var/lib/buildkite-agent/.ssh/`
and add the host key of github.com to its `known_hosts`.

**2.6 Start, then confirm.**

```bash
sudo systemctl enable --now buildkite-agent
sudo journalctl -f -u buildkite-agent      # "Registering agent…" then "Waiting for work"
```

The agent appears under Agents in the UI with its queue tag. Repeat for the
second host with its own token, name and queue.

---

## 3. The pipeline

One pipeline per repository (`vbpub`, later `dstdns`). Its stored
configuration is one step — it generates the real steps and uploads them:

```yaml
env:
  RUN_GATE_QUEUE: "gate-<host>"
steps:
  - label: ":pipeline: lanes"
    command: "run-gate-project/tools/buildkite/pipeline.sh run-gate-project | buildkite-agent pipeline upload"
```

(The paths are vbpub's: the script and the project directory it is asked
about are both `run-gate-project/…`. `RUN_GATE_LANES`, when the build carries
one, is inherited from the build's environment by this step, so the generator
sees it without any further plumbing. **A build's own env overrides the
pipeline's**, so a build created with `RUN_GATE_QUEUE` in its env — what
`BK_QUEUE` does in §4.2 — runs on that queue instead of the default above,
with no pipeline edit.)

**`run-gate-project/tools/buildkite/pipeline.sh` is that generator, and it is
written** (seam 2 in §6; tested by `tests/test_buildkite_tools.py`). It reads
`./run-gate.py --list` — the stable, machine-readable lane listing that
`CONSUMERS.md`'s anti-goals name as the ONLY way a consumer may read lane
metadata — and prints, on stdout, one step per selected lane. It never opens
`run-gate.toml`, starts no container, makes no network call and takes no
`--dry-run`: running it *is* the rehearsal, because output is all it does.

```
usage: pipeline.sh [PROJECT_DIR]        (default: .)
       pipeline.sh --help
```

`PROJECT_DIR` is the directory holding `run-gate.py`, **as the agent will see
it** relative to the checkout root: it is used both to invoke `--list` here and
verbatim inside the emitted `command` and `artifact_paths` (a plain `.` emits
neither a `cd` nor a path prefix). Its environment contract:

| variable | required | meaning |
|---|---|---|
| `RUN_GATE_QUEUE` | **yes** | the agent queue (`gate-<host>`); also names the concurrency group. Unset or empty → exit 2 naming it, never a default |
| `RUN_GATE_LANES` | no | space-separated lane names. A name the listing does not show → exit 2 naming it (and listing what it does show). Empty/unset → every lane, see below |
| `RUN_GATE_TIMEOUT_MINUTES` | no | per-step timeout, default 300; must be a positive integer |

Exit 0 means the document was emitted; **exit 2 is always a refusal** — bad
environment, unknown lane, a project with no executable `run-gate.py`, a
`--list` that failed, or a listing that no longer matches the three-column
contract the generator was written against (`name<TAB>kind<TAB>environment`,
verified against `run-gate.py` rev 33 `cmd_list()`). It refuses rather than
guesses: a fourth column would mean the contract moved.

**`--list` cannot say which lanes are remote-capable — there is no such
column** — and inventing one in the generator would be exactly the second
parser of `run-gate.toml` the anti-goal forbids. So an empty `RUN_GATE_LANES`
selects **every** lane the listing shows (both of run-gate's kinds, `command`
and `assay`); a build that wants a subset names it. If a remote-capability
column is ever wanted, it belongs in `--list` itself, as a run-gate change.

A generated step, verbatim from the generator (this is the §3 shape; every key
below is emitted, in this order):

```yaml
steps:
  - label: "run-gate: mutation on gate-alpha"
    command: "cd <project> && ./run-gate.py mutation"
    agents:
      queue: "gate-alpha"
    concurrency: 1
    concurrency_group: "gate/gate-alpha"
    timeout_in_minutes: 300
    artifact_paths:
      - "<project>/.assay/**/*"
      - "<project>/.run-gate/history.json"
    env:
      RUN_GATE_LANE: "mutation"
```

Three of those lines carry the doctrine:

- `concurrency: 1` + `concurrency_group: "gate/$RUN_GATE_QUEUE"` is the
  one-container rule, enforced by the queue for every trigger, not by
  agents remembering it. One queue per host means one group per host.
- `command` is the same argv every other consumer uses. No CI-only script
  runs the tests; if the lane is wrong, it is wrong for everyone and fixed
  once.
- `artifact_paths` is the contract by which the result travels back: the
  verdict, the progress file and the evidence directory under `.assay/`, and
  the RG-27 history entry. A lane that should run remotely declares
  `artifacts = [...]` in its `run-gate.toml` entry so the paths are stated
  in one place (`LANE-AUTHORING.md` §5).

Which lanes a build runs is an input, not a fixed list: pass
`RUN_GATE_LANES="mutation nightly-properties"` in the build's `env` (which is
exactly what `bk-lane.sh run` does, §4) and let the generator select; an empty
value means every lane the listing shows, per the paragraph above.

---

## 4. Give this host access

**4.1 An API token** for the controller host (personal settings → API access
tokens). Scopes: `read_builds`, `write_builds`, `read_artifacts`,
`read_pipelines`. Store it outside every repository:

```bash
install -m 700 -d ~/.config/buildkite
umask 077; printf '%s\n' '<token>' > ~/.config/buildkite/api-token
curl -fsS -H "Authorization: Bearer $(cat ~/.config/buildkite/api-token)" https://api.buildkite.com/v2/user
```

**4.2 `bk-lane.sh` — trigger, wait, collect.** The sketch that used to sit here
is now a real script, `run-gate-project/tools/buildkite/bk-lane.sh` (seam 4 in
§6). It only creates builds and reads their results: the lane still runs as
`./run-gate.py <lane>` on the agent, and this script never opens
`run-gate.toml` — lane *selection* travels as the build's
`env.RUN_GATE_LANES`, which the §3 generator then reads.

```
bk-lane.sh [--dry-run] run <lane>...                  create a build for HEAD, wait
bk-lane.sh [--dry-run] status <build-number>          print the build's state
bk-lane.sh [--dry-run] collect <build-number> <dir>   download the artifacts
bk-lane.sh --help
```

- `run` takes `commit` and `branch` from git in the current work tree (a
  detached HEAD is refused — Buildkite requires a branch), POSTs the build with
  `env.RUN_GATE_LANES` set to the named lanes, then polls
  `GET …/builds/{number}` every `BK_POLL_SECONDS` until the state is terminal.
  **Terminal states: `passed`, `failed`, `canceled`, `blocked`, `skipped`,
  `not_run`, `waiting_failed`.** Exit 0 only on `passed`; 1 for any other
  terminal state; 2 for a refusal.
- `collect` reads the build (for its commit), lists its artifacts, and
  downloads each into `<dir>/<commit>/<artifact path>` — commit-addressed, so a
  late result can still be matched to what produced it. Artifact paths that are
  absolute or contain `..` are refused rather than written.
- `status` prints the state and exits 0.

| variable | required | meaning |
|---|---|---|
| `BK_ORG` | **yes** | organization slug; no default |
| `BK_PIPELINE` | **yes** | pipeline slug; no default |
| `BK_TOKEN_FILE` | no | default `~/.config/buildkite/api-token`; **must be mode 0600 or the script refuses** (exit 2, naming the mode it found) |
| `BK_POLL_SECONDS` | no | poll interval for `run`, default 30 |
| `BK_QUEUE` | no | `run` only: sent as `env.RUN_GATE_QUEUE` in the create-build body beside `RUN_GATE_LANES`. A build's env overrides the pipeline's (§3), so this moves ONE run to another host's queue with no pipeline edit; unset, the key is not sent at all and the pipeline's default queue stands |

Dependencies are `bash`, `coreutils`, `git`, `curl` and `python3` (stdlib
`json` only). **`jq` is deliberately not assumed** — the hosts do not need a
second install to be usable.

The REST paths it uses, verified against the vendor docs on 2026-09-03:

```
POST /v2/organizations/{org.slug}/pipelines/{pipeline.slug}/builds
GET  /v2/organizations/{org.slug}/pipelines/{pipeline.slug}/builds/{build.number}
GET  /v2/organizations/{org.slug}/pipelines/{pipeline.slug}/builds/{build.number}/artifacts
```

The third is "List artifacts for a build" from
<https://buildkite.com/docs/apis/rest-api/artifacts>, read 2026-09-03; each
artifact object in the response carries `id`, `job_id`, `url`, `download_url`,
`state`, `path`, `dirname`, `filename`, `mime_type`, `file_size` and `sha1sum`
(the deprecated `glob_path`/`original_path` are `null`). The collector uses
`path` and `download_url` and nothing else.

**What has actually been run.** `--dry-run` prints every curl invocation the
verb would make — with the bearer token replaced by `<redacted>` — and makes
**no** network call; that is the path `tests/test_buildkite_tools.py` exercises,
and no test in this repo touches the network or Buildkite. **The live path has
never been executed: no build has been created by this script, and no artifact
has been downloaded by it.** Treat the first real `bk-lane.sh run` as the
first test of the live path, and start it on the cheapest lane (§8).

```bash
BK_ORG=<org> BK_PIPELINE=vbpub ./tools/buildkite/bk-lane.sh --dry-run run selftest
```

**4.3 Collect artifacts.** `bk-lane.sh collect <build-number> <dir>` downloads
the build's artifacts — the `artifact_paths` the §3 step declared, i.e. the
verdict, the progress file and the evidence directory under `.assay/`, plus the
RG-27 `history.json` — into `<dir>/<commit>/…`. Handing the verdict onward is
still a manual step and is deliberately NOT in the script: run-gate's history
store for the lane, a `.assay-inbox/`-style drop for dstdns (the pattern its
release notifications already use), or assay's Tier 3 ledger for fuzz findings.

**4.4 Asynchronous use.** Fire the build and do not wait — `bk-lane.sh run`
prints the build number before it starts polling, so an operator (or a later
session) can `Ctrl-C` and come back with `status` and `collect` against that
number. The result is the artifact, keyed by commit. Buildkite's own
notifications can post to a webhook, but this host has no public endpoint, so
polling is the honest first design. Collecting by *commit* rather than by build
number would need a build search (`GET …/builds?commit=…`); that is not
implemented, and `collect` takes the build number for now.

---

## 5. The image on the remote host

run-gate's central config names the environment image as `tester-unified:local`
(`run-gate.toml` at the repo root). Two ways for a remote host to have it:

- **Build from the checkout.** `docker build -f tester-unified/Dockerfile -t
  tester-unified:local .` from the repo root, in an agent `pre-command` hook or
  as the pipeline's first step on each host. This is what
  `nyxloom/tools/remote-mutation-audit-host.sh` does today (line 93), and it
  is fresh-clone safe: the base image is already on GHCR and the test closure
  is derived from the projects' `pyproject.toml` files. Cost: minutes per
  build; cache it by commit of `tester-unified/Dockerfile` plus the pyprojects.
- **Publish the image to GHCR and pull it.** cmru already has the verbs:
  `cmru.handlers oci-image-build` / `oci-image-push` run `docker buildx bake`
  against a bake HCL file and log in with `REGISTRY`, `GITHUB_USERNAME` and
  `GITHUB_PUSH_PAT` from the environment. What is missing is a small
  `tester-unified/` cmru project: a `docker-bake.hcl` naming the target and
  the tag `ghcr.io/<owner>/tester-unified:<tag>`, a `cmru.toml` with a
  `[steps.push]` entry calling the two handlers, and the PAT in the release
  environment. The remote host's hook then does
  `docker pull ghcr.io/<owner>/tester-unified@sha256:<digest> && docker tag …
  tester-unified:local`. Pin by digest, never by tag, and record the digest
  beside the assay pin in the central config once run-gate learns an
  `image_digest` key (backlog, §6).

Recommendation: start with **build from the checkout** on both hosts (zero
new config, provenance identical to the dev host's), and publish to GHCR when
the build time on the hosts starts to matter. vbpub is public, so publishing
carries no secret; the base image is public already.

---

## 6. Integration seams (in build order)

Seams 1, 2 and 4 are the ones that need no remote host, and they have landed —
**dry-run tested only**: `tests/test_buildkite_tools.py` runs both scripts with
no network, no Buildkite account and no container, and nothing here has yet run
against a live agent. Seams 3, 5 and 6 are untouched.

| # | seam | owner | status | shape |
|---|---|---|---|---|
| 1 | Artifacts contract | run-gate (docs) + each lane | **landed (dry-run tested)** | every remote-capable lane declares `artifacts` covering `.assay/<lane>/**`, `.assay/progress-<lane>.jsonl`, `.run-gate/history.json` (`LANE-AUTHORING.md` §5); the generator emits the matching `artifact_paths` globs per step, asserted in the suite |
| 2 | Pipeline generator | `run-gate-project/tools/buildkite/pipeline.sh` | **landed (dry-run tested)** | consumes `./run-gate.py --list` only (no second parser of `run-gate.toml`); emits the §3 step shape; selects by `RUN_GATE_LANES`; refuses (exit 2) an unknown lane, a missing `RUN_GATE_QUEUE`, or a listing wider than the three documented columns |
| 3 | Image provenance | `tester-unified/` cmru project, `run-gate.toml` central config | not started | build-from-checkout first; GHCR publish via `oci-image-push` later; `image_digest` key in the environment table (new RG item) |
| 4 | Trigger + collector | `run-gate-project/tools/buildkite/bk-lane.sh` on the controller host | **landed (dry-run tested; no live build yet)** | `run` creates + waits, `status` reads, `collect` downloads into a commit-addressed directory; token file must be 0600. Still open: feeding run-gate history with a `host` field (new RG item), dstdns's inbox, and lookup by commit instead of build number (§4.4) |
| 5 | Async evidence | assay Tier 3 (A-O08 shape) | not started | fuzz findings and nightly campaign verdicts land as attested, commit-bound evidence; assay proves staleness, never truth |
| 6 | Stack-needing lanes on a remote host | ciu (v8 `ciu gate`, or v7 `ciu up` today) | not started | the Buildkite step runs `ciu` LOCALLY on that host under the agent user; ciu stays the local orchestrator, Buildkite the trigger |

Before seam 3 or 6 is designed, measure the two hosts: cores, RAM, Docker
version, whether they carry production, their outbound network policy, and
the account's plan limits.

---

## 7. ciu on the same hosts

The hosts also get root SSH for **ciu** (push/activate in v7.11, the v8
push/activate/`ciu ssh` surface later). The two uses do not interfere:

- ciu's remote deployment goes over SSH as root and writes under ciu's own
  directories; the Buildkite agent runs as `buildkite-agent`, reads only its
  build path, and talks outbound only.
- A lane that needs a deployed instance runs `ciu` from the Buildkite job,
  on the host, as the agent user — so that user needs the ciu wheel
  installed and the docker group, nothing more. Zero-instance projects
  (`ciu gate` on a project with no stacks, v8 S16.11) need only run-gate.
- Do not let a Buildkite job SSH anywhere. If a step needs another host,
  that is a second step on that host's queue.

---

## 8. Checklist for the first enrollment

- [ ] Host facts measured (§6, last paragraph) and written into the
      estate's host memory.
- [ ] Agent installed, token in the config file at mode 0600, queue named
      `gate-<host>`, `spawn=1`, slice drop-in in place.
- [ ] `buildkite-agent` in the docker group; `docker ps` works as that user.
- [ ] Image present (§5, build from checkout) and `./run-gate.py doctor`
      clean from a fresh clone on the host.
- [ ] Pipeline created with the §3 generator-upload step and `RUN_GATE_QUEUE`
      set; `RUN_GATE_LANES=selftest run-gate-project/tools/buildkite/pipeline.sh
      run-gate-project` first checked by eye on this host — it needs no agent.
- [ ] First build runs the cheapest lane (`lint` or `selftest`) and its
      artifacts appear.
- [ ] API token on this host at `~/.config/buildkite/api-token`, mode 0600;
      `bk-lane.sh --dry-run run selftest` inspected, THEN the same command
      without `--dry-run` — **this is the first live exercise of that script**
      (§4.2), so read its output rather than trusting it.
- [ ] `bk-lane.sh collect <number> <dir>` fetches that build's artifacts —
      also a first live exercise.
- [ ] Second host enrolled the same way with its own queue; two builds for
      two hosts run concurrently, two builds for one host do not.
