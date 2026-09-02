# run-gate — remote and asynchronous lanes on Buildkite

Sibling of **`CONSUMERS.md`** (adoption mechanics) and **`LANE-AUTHORING.md`**
(what makes a lane good). This file is the operator's manual for running
lanes on hosts other than the one you are sitting at: enrolling a host as a
Buildkite agent, giving this host access to trigger and collect, and how the
result plugs into run-gate, assay and ciu. Written 2026-09-02; the Buildkite
commands were read from the vendor's docs that day and are quoted as found —
**verify the apt channel and the API paths once against the live docs before
the first enrollment**, they are the two things most likely to have moved.

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
configuration is two lines:

```yaml
steps:
  - label: ":pipeline: lanes"
    command: "tools/buildkite/pipeline.sh | buildkite-agent pipeline upload"
```

`tools/buildkite/pipeline.sh` (not written yet; see §6) parses
`./run-gate.py --list` — the stable, machine-readable lane listing that
`CONSUMERS.md`'s anti-goals name as the ONLY way a consumer may read lane
metadata — and emits one step per lane the build asks for. The shape of a
generated step:

```yaml
  - label: "run-gate: mutation on <host>"
    command: "cd <project> && ./run-gate.py mutation"
    agents:
      queue: "gate-<host>"
    concurrency: 1
    concurrency_group: "gate/<host>"
    timeout_in_minutes: 300
    artifact_paths:
      - "<project>/.assay/**/*"
      - "<project>/.run-gate/history.json"
    env:
      RUN_GATE_LANE: "mutation"
```

Three of those lines carry the doctrine:

- `concurrency: 1` + `concurrency_group: "gate/<host>"` is the
  one-container rule, enforced by the queue for every trigger, not by
  agents remembering it.
- `command` is the same argv every other consumer uses. No CI-only script
  runs the tests; if the lane is wrong, it is wrong for everyone and fixed
  once.
- `artifact_paths` is the contract by which the result travels back: the
  verdict, the progress file and the evidence directory under `.assay/`, and
  the RG-27 history entry. A lane that should run remotely declares
  `artifacts = [...]` in its `run-gate.toml` entry so the paths are stated
  in one place (`LANE-AUTHORING.md` §5).

Which lanes a build runs is an input, not a fixed list: pass
`RUN_GATE_LANES="mutation nightly-properties"` in the build's `env` and let
the generator select; an empty value means every lane the project marks for
remote execution.

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

**4.2 Trigger a build for a commit (synchronous use).** From the REST API as
read on 2026-09-02: `POST /v2/organizations/{org}/pipelines/{pipeline}/builds`
with `commit` and `branch` required; poll
`GET …/builds/{number}` until `state` leaves `scheduled`/`running`
(`passed`, `failed`, `canceled`, `blocked` are terminal for our purposes).

```bash
#!/usr/bin/env bash
# bk-lane.sh — trigger one run-gate lane on a remote host and wait for it. UNTESTED SKETCH.
set -euo pipefail
org=<org>; pipeline=vbpub; commit=$(git rev-parse HEAD); lanes=${1:?lane names}
tok=$(cat ~/.config/buildkite/api-token)
api=https://api.buildkite.com/v2/organizations/$org/pipelines/$pipeline/builds
number=$(curl -fsS -X POST "$api" -H "Authorization: Bearer $tok" -H 'Content-Type: application/json' \
  -d "{\"commit\":\"$commit\",\"branch\":\"$(git rev-parse --abbrev-ref HEAD)\",\"message\":\"run-gate: $lanes\",\"env\":{\"RUN_GATE_LANES\":\"$lanes\"}}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["number"])')
echo "build $number for $commit"
while :; do
  state=$(curl -fsS "$api/$number" -H "Authorization: Bearer $tok" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
  case $state in scheduled|running|waiting) sleep 30 ;; *) echo "state: $state"; break ;; esac
done
```

**4.3 Collect artifacts.** The build's job objects carry an `artifact_url`;
the artifacts endpoint under the build lists files with a download URL each.
The collector (§6) downloads `.assay/**` for the commit into a local
commit-addressed directory and hands the verdict to whoever asked: run-gate's
history store for the lane, a `.assay-inbox/`-style drop for dstdns (the
pattern its release notifications already use), or assay's Tier 3 ledger for
fuzz findings.

**4.4 Asynchronous use.** Fire the build and do not wait: the result is the
artifact, keyed by commit. A later `bk-lane.sh --collect <commit>` (or the
collector on a timer) fetches it. Buildkite's own notifications can post to
a webhook, but this host has no public endpoint, so polling by commit is the
honest first design.

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

## 6. Integration seams (nothing implemented yet, in build order)

| # | seam | owner | shape |
|---|---|---|---|
| 1 | Artifacts contract | run-gate (docs) + each lane | every remote-capable lane declares `artifacts` covering `.assay/<lane>/**`, `.assay/progress-<lane>.jsonl`, `.run-gate/history.json`; one `artifact_paths` glob per step |
| 2 | Pipeline generator | `run-gate-project/tools/buildkite/pipeline.sh` | consumes `./run-gate.py --list` only (no second parser of `run-gate.toml`); emits the step shape in §3; selects by `RUN_GATE_LANES` |
| 3 | Image provenance | `tester-unified/` cmru project, `run-gate.toml` central config | build-from-checkout first; GHCR publish via `oci-image-push` later; `image_digest` key in the environment table (new RG item) |
| 4 | Trigger + collector | `bk-lane.sh` on the controller host | create, wait or return, collect by commit into a commit-addressed directory; feed run-gate history with a `host` field (new RG item) and dstdns's inbox |
| 5 | Async evidence | assay Tier 3 (A-O08 shape) | fuzz findings and nightly campaign verdicts land as attested, commit-bound evidence; assay proves staleness, never truth |
| 6 | Stack-needing lanes on a remote host | ciu (v8 `ciu gate`, or v7 `ciu up` today) | the Buildkite step runs `ciu` LOCALLY on that host under the agent user; ciu stays the local orchestrator, Buildkite the trigger |

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
- [ ] Pipeline created with the two-line upload step; first build runs the
      cheapest lane (`lint` or `selftest`) and its artifacts appear.
- [ ] API token on this host at `~/.config/buildkite/api-token`, `bk-lane.sh`
      triggers and waits for that lane.
- [ ] Second host enrolled the same way with its own queue; two builds for
      two hosts run concurrently, two builds for one host do not.
