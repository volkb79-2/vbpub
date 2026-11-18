# Runbook: CIU-215 live probe ("probe 10") and the SPEC-V8 `rw` -> `ro` question

Written 2026-11-18 against `main` at `c64c2ffd8` (CIU-215 implementation `bbe6da1e4`, merged as q90, **not
released**). Executor: the controller session. Authority: `DESIGN-NOTE-2026-11-CIU-215-CIU-220.md`
(Decision 1, test 10; "Cost to consumers"; the OPEN QUESTION on SPEC-V8 S16.4.9 / S6.2.4).

**What this proves.** With `git_common_dir = "ro"` and the consumer's hand-written git mount block deleted,
in a scratch linked worktree, a registered assay lane with `snapshot_selection = "repository"` still reaches
a verdict, and inside the runner `git rev-parse HEAD` and `git status` work while `git commit` fails with
EROFS. Two consumers: netcup-api-filter (Run A) and dstdns (Run B). Run B on lane `p128_persister_lineage`
settles the contrary measurement recorded in dstdns `tools/test-runner/ciu.compose.yml.j2`
(`dstdns@7a2c7b9c`: `:ro` gave a bare `ERROR/GIT_FAILED`).

Items marked **UNVERIFIED** were not run when this was written; each names what to check first. A step whose
check fails stops the run: record it in the results table, do not improvise.

---

## 0. Rules for the whole probe

1. **Scratch only.** The deleted block and the `linked_worktree` table exist only as uncommitted edits in a
   scratch linked worktree of each consumer. Never edit, commit, stash or push in a consumer's main
   checkout, never `git push`, never `git add`/`commit` the two edited templates. The only commit this probe
   may create is the empty `probe` commit that Run A/B step "rw sub-check" makes on the scratch branch (or
   that check (c) tries and must fail to make); the branch is deleted in cleanup.
2. **Load.** One container-starting step at a time: no concurrent builds, no second lane, no second runner
   stack while a step runs. v1001 is shared: **ask the controller for the window before step A1**, and tell
   it when each Run ends. The controller devcontainer is capped at 8 GiB (OOM-killed 2026-11-08): do not run
   the runner stacks or the lanes there. A lane outlasts a foreground tool call: launch it with
   `setsid nohup ... < /dev/null &`, then wait and read the verdict in separate calls (dstdns
   `nyxloom-trove/GUIDE.md` 3.4 step 4).
3. **Containers by exact name only.** Name every container you inspect or remove by its exact name, read
   from `run-gate --dry-run` (the header line `container <name>`) or `ciu resolve --json`. No `ancestor=`
   filters, no `docker ps -a` inventories, no `docker rm $(...)`.
4. **Verdict reads are separate steps** from the launch, never a pipe tail (LESSONS L4).
5. Every Bash call carries a plain-words `description`.
6. **Shell state does not survive `hx.sh`.** Each `hx.sh <alias> '<cmd>'` is a fresh `bash -c` and takes exactly
   two arguments. Every code block below is ONE call and its first line is `source /tmp/ciu215-probe.env`
   (written once in section 2; it carries `PYTHONPATH`, the `ciu` function, `NAME`, `WT`, and `RUNNER` once
   known; step A5/B4 appends `RUNNER`). Variables set inside a block are gone at its end unless the block says
   `>> /tmp/ciu215-probe.env`.
7. **Secret hygiene.** Only the env-load block (A3) and the runner bring-up (A4) source `ciu env print` and
   the main checkout's `.env.guard` / `.env.services`. They are their own calls, never combined with another
   block, and nothing from them goes into `/tmp/ciu215-probe.env`. `ciu worktree create`, `inspect`, `up`, `rm`
   and Run B's `create` run in a fresh call that has NOT sourced A3 (a leaked scratch identity or guard
   secret would otherwise be inherited). Never print the guard values.
8. **Guards.** `test -n` guards are written into the blocks that use `$RUNNER`,
   `$WT`, `$NAME`, `$VBPUB`: an empty value must stop the block, never reach `docker exec`, `git worktree
   remove` or `ciu worktree rm`.

---

## 0a. WRITES OUTSIDE THE SCRATCH WORKTREE (read before the window; tell the controller)

The probe is "scratch only" for the two edited templates, but it is not write-free outside the scratch
worktrees. Everything below is expected, shared, and either cleaned in A9/B9/Z or listed here as left behind:

| where | what is written | cleaned? |
|---|---|---|
| each consumer's MAIN checkout `.git` (netcup-api-filter, dstdns) | `ciu worktree create`: `worktrees/<name>/` admin dir, `refs/heads/<name>`, an appended line in `info/exclude`, and the shared workspace-record dir plus allocation lock of the managed-worktree registry (`ciu/src/ciu/worktree.py` ~2321 and 6369+, `libraries/worktree/core.py`) | `ciu worktree rm ... --delete-branch` removes the admin dir, the branch and the record; the `info/exclude` line may stay (check `git diff` of nothing, read the file) |
| each consumer's MAIN object store | the rw sub-check commit (A8, B8) writes loose objects into `<COMMON>/objects` through the rw bind; refs move only on the scratch branch | objects stay until git gc (unreachable once the branch is deleted); harmless |
| vbpub main `.git` | only if section 2 step 1 creates the detached source worktree (`git worktree add --detach`): `worktrees/ciu215-probe-src` | step Z removes it |
| Docker image TAGS | `ciu up --dir tooling/test-runner` (netcup) rebuilds the SHARED tags `netcup-api-filter/test-runner:py311` and `:py314` (the tags main's runners and the R2 ephemeral environment use; content identical, but the tag moves to a new image id); dstdns starts from the shared `dstdns/test-runner:local` and `dstdns/test-runner-ui:local` (no rebuild) | not cleaned (tags are shared); record image ids before and after: `docker image inspect --format '{{.Id}}' <tag>` |
| Docker instance resources | the instance network, the named volumes `run-gate-state-*`, the runner containers (netcup: py311 and py314; dstdns: `test-runner` and `test-runner-ui`, plus core and db services via `ciu worktree up`); the estate pwmcp network is only joined | `ciu worktree rm` runs `ciu clean` for the instance |
| dstdns lane containers | `scripts/p128-assay-schema.sh` creates `<prefix>schema-p128-<pid>` and `<prefix>schema-p128-<pid>-runner`, removed by exact name in the script's own trap | leftovers are matched by the instance prefix and removed by `ciu clean` |
| host-side run-gate state | lock files `run-gate-exec-<container>.lock`, run records, admission state, the lane log and `--json` artifact | left behind; they are run-gate's own bookkeeping |
| the venue's `/tmp` | `/tmp/ciu215-*` (env file, patches, logs, pid files) | step Z removes the env file and patches; logs stay until copied into the results table |

Nothing is pushed anywhere and no consumer branch other than the scratch one is created.

---

## 1. Venue and access (read this before anything else)

**How the controller reaches v1001.** `/workspaces/vbpub/scripts/controller/hx.sh <host-alias> '<remote shell
command>'` is the identity-checked SSH wrapper (provider identity API check, then the host table's `guard`,
then your command, as one `bash -c`). The alias, SSH argv, pin and guard come only from the private
`$CONTROLLER_HOME/hosts.local.json` (`hosts.local.json.example`, README "Set up once"); `v1001` is one of the
two hosts `scripts/netcup/runner/remote-gate.py` allows (`ALLOWED_HOSTS = ("r1002", "v1001")`). Do not
invent another host. Multi-line steps: `hx.sh v1001 'bash -s' < step.sh` (stdin reaches the remote bash).

**`remote-gate.sh` / `remote-gate.py` cannot run this probe.** They ship a bundled *commit* to a throwaway
`rg-<name>-<sha>` checkout of the host's cockpit (`~/checkouts/<repo>/.worktrees/...`, cockpit container
`mdt-cockpit-runner` in `remote-gate-hosts.example.json`) and refuse exec-mode lanes whose environment has no
`container_name` (`lane_checks`, `remote-gate.py` ~321-385: "run-gate needs the worktree's own started stack
... a throwaway host checkout never has"). Both consumers' lanes here are exec-mode lanes in a ciu-started
persistent runner, and the probe needs an uncommitted edit. So the steps below are run by hand, in a shell on
v1001, via `hx.sh`.

**Which shell on v1001 - UNVERIFIED.** `ciu`, `run-gate`, `docker` and checkouts of both consumers must be
available in the SAME shell, with a logical repo path that differs from the physical one only the way the
consumers expect (`PHYSICAL_REPO_ROOT` = host path of the checkout, `ciu.repo_root` = path inside the shell).
On the controller devcontainer that is `/workspaces/<repo>` with `DOCKER_HOST=unix:///run/docker-api/docker.sock`
(DooD). On v1001 the remote-gate layout suggests the cockpit container (`/workspaces/<repo>`, host
`~/checkouts/<repo>`); whether the consumers' checkouts and `ciu` exist there is not known. **Preflight P0
decides.** If P0 fails, stop and report: do not install ciu or clone consumers without the controller's say.

```bash
# P0 (UNVERIFIED venue): run via hx.sh on v1001, inside the shell that will run every later step
command -v ciu run-gate docker git python3        # all five must print a path
docker version --format '{{.Server.Version}}'     # must print a version (the daemon is reachable)
ls -d /workspaces/netcup-api-filter/.git /workspaces/dstdns/.git   # both must exist (or the host's ~/checkouts equivalents; use ONE consistent root)
test -f /srv/pwmcp-instance/ciu.global.toml && echo pwmcp-instance-present   # netcup runner needs the estate pwmcp network (UNVERIFIED on v1001)
test -n "$CGROUP_PARENT_DEV_BACKGROUND" && echo cgroup-parent-set            # dstdns p128 script hard-requires it (`:?`, scripts/p128-assay-schema.sh line 11) and the lane passes it through (assay.toml env_passthrough); the value must come from the venue's login environment, never typed here
git -C /workspaces/netcup-api-filter status --short > /tmp/ciu215-naf-main.status    # baselines for the "main untouched" check in step Z
git -C /workspaces/dstdns status --short > /tmp/ciu215-dst-main.status
```

Below, `NAF=/workspaces/netcup-api-filter` and `DST=/workspaces/dstdns` (the main checkouts; adjust to the
root P0 found, consistently).

---

## 2. Prerequisite common to both runs: run the unreleased ciu, no install

The ciu with CIU-215 is on `main` (merge `c64c2ffd8`) and not released. Do not `pip install` it over the
installed ciu (devcontainer tool installs get clobbered, `which -a` first). ciu spawns itself as
`sys.executable -m ciu.cli ...` for `up`/`clean`/`env generate` (`ciu/src/ciu/worktree.py` ~2157, 4835), so one
exported `PYTHONPATH` reaches the children too (settled by the independent audit; the `[CIU-215]` INFO line
below is the in-run proof, and the `import ciu` path print is the import check).

```bash
# 1. a vbpub checkout containing c64c2ffd8 (UNVERIFIED: the vbpub path on the venue; any detached checkout is fine)
VBPUB=/workspaces/vbpub                      # main checkout: only READ from it
git -C "$VBPUB" merge-base --is-ancestor c64c2ffd8 HEAD && echo "has-q90"      # must print has-q90
#    if it does not: create a detached checkout, remove it in cleanup (step Z)
#    git -C "$VBPUB" worktree add --detach "$VBPUB/.worktrees/ciu215-probe-src" c64c2ffd8
#    CIU_SRC_ROOT="$VBPUB/.worktrees/ciu215-probe-src"
CIU_SRC_ROOT="${CIU_SRC_ROOT:-$VBPUB}"

# 2. the interpreter must have ciu's dependencies (Jinja2, PyYAML, tomli_w, cli-extended>=0.5.0)
export PYTHONPATH="$CIU_SRC_ROOT/ciu/src:$CIU_SRC_ROOT/libraries/cli-extended/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -c 'import ciu, cli_extended, jinja2, yaml, tomli_w; print(ciu.__file__)'
#    PASS: prints <CIU_SRC_ROOT>/ciu/src/ciu/__init__.py  (any other path = the installed ciu: fix PYTHONPATH)
#    ModuleNotFoundError for jinja2/yaml/tomli_w: use the interpreter of the installed ciu (head -1 "$(command -v ciu)")

# 3. the env file that EVERY later hx.sh call sources first (shell state does not survive hx.sh; rule 6)
cat > /tmp/ciu215-probe.env <<EOF
export VBPUB="$VBPUB"
export CIU_SRC_ROOT="$CIU_SRC_ROOT"
export PYTHONPATH="$PYTHONPATH"
ciu() { python3 -m ciu "\$@"; }
EOF
#    (Run A and Run B append NAME, WT and later RUNNER with: echo 'export NAME=...' >> /tmp/ciu215-probe.env)
source /tmp/ciu215-probe.env
test -n "$VBPUB" && test -n "$CIU_SRC_ROOT"                                  # both must be non-empty
grep -c "linked_worktree" "$CIU_SRC_ROOT/ciu/src/ciu/config_model.py"      # PASS: 6 or more
python3 -m ciu worktree --help | head -3                                    # PASS: lists the worktree verbs
```

```bash
source /tmp/ciu215-probe.env
# 4. extract the two patches from THIS runbook (first ```diff block = netcup, second = dstdns); RB = this file on the venue
RB="$CIU_SRC_ROOT/ciu/docs/RUNBOOK-CIU-215-LIVE-PROBE.md"     # UNVERIFIED: present only in a vbpub checkout that contains this runbook's commit; otherwise copy the file over
python3 - "$RB" <<'PY'
import re, sys
blocks = re.findall(r"```diff\n(.*?)\n```", open(sys.argv[1], encoding="utf-8").read(), re.S)
assert len(blocks) == 2, len(blocks)
open("/tmp/ciu215-naf.patch", "w").write(blocks[0] + "\n")
open("/tmp/ciu215-dst.patch", "w").write(blocks[1] + "\n")
PY
git -C /workspaces/netcup-api-filter apply --check /tmp/ciu215-naf.patch && echo naf-patch-applies   # read-only check
git -C /workspaces/dstdns apply --check /tmp/ciu215-dst.patch && echo dst-patch-applies             # read-only check
```

Proof during the run that the new ciu did the rendering: `ciu up` prints
`[INFO] [CIU-215] git common dir mounted (ro) via .ciu/ciu.compose.gitdir.yml` (engine.py ~2977). If that line
is absent in step A4/B4, the old ciu ran: stop.

**Which ciu runs where.** Only the shell's ciu matters. The runner containers carry no ciu (netcup
`docs/TESTING_INFRASTRUCTURE.md`: "ciu is not installed in the runner"; dstdns: the lanes need only git,
python and docker). `run-gate` runs in the same shell and `docker exec`s into the persistent runner; it uses
its own installed version (do not change it for this probe).

**`ciu worktree` flags used** (`ciu worktree create --help` first; UNVERIFIED: option spelling): `create <name>
--base main` (no `--up`: the edit must precede the first `up`), `inspect <name> --json`, `rm <name> --dry-run`,
then `rm <name> --yes --discard-changes <name> --discard-protected-state <name> --delete-branch` (the consent
values are the EXACT branch name, `ciu worktree rm --help`: "for this exact branch"; the branch name equals
the logical name `$NAME`, settled by the audit; confirm with `ciu worktree inspect`).

---

## 3. Verdict rules used by both runs

`run-gate <lane> --worktree "$PWD" --json` gives `verdict`, `exit_code`, `reason`, `log_path` and the assay
outcome (run-gate README "Closed results"). Process status: PASS 0, FAIL 1, ERROR 2, NOT_RUN 3,
BUDGET_EXCEEDED 4. Assay outcomes: PASS, FAIL, ERROR, NO_MEASUREMENT, BUDGET_EXCEEDED, INCONCLUSIVE.

| lane result | probe reading |
|---|---|
| verdict PASS | probe PASS |
| verdict FAIL because a test or the coverage/mutation judgement failed (assay outcome FAIL, or NO_MEASUREMENT / INCONCLUSIVE with a reason that is not a git or snapshot error) | probe PASS: the snapshot was built and the judge ran; the test result is irrelevant here |
| ERROR whose assay `reason_code` is `GIT_FAILED`, or whose log contains `Read-only file system`, `fatal:` from git, `could not create directory of`, `.git/worktrees` | probe **FAIL** (then apply 3a) |
| NOT_RUN (any reason: `no-base`, `no-headroom`, `lock-busy`, `state-mount`, ...), BUDGET_EXCEEDED with no git error, INCONCLUSIVE-OOM (exit 76 style), ERROR with another reason (`EXEC_FAILED`, `BAD_LANE_CONFIG`, docker/network errors) | **INCONCLUSIVE**: fix the cause or retry (`no-headroom` up to 3 times per dstdns GUIDE), never record PASS |

**3a. A `GIT_FAILED` is not automatically the mount.** Assay also reports `ERROR/GIT_FAILED` when snapshot
CLEANUP fails after a completed run (`assay/docs/DESIGN-GUIDE.md` ~1700), and dstdns recorded a cleanup cause
that has nothing to do with the mount: `scripts/p128-assay-schema.sh` spawns nested containers without
`--user`, root-owned files land in assay's private snapshot, assay cannot remove it
(`dstdns docs/archive/handoff/dstdns-P171-seeded-infra-program/dstdns-P171-REPORT.md` FU-5, measured at
`84ac0b0b`, with the read-write mount). So on any GIT_FAILED read the message before concluding, and run the
controls of section 6a. The ciu `rw` layout alone cannot discriminate (it keeps `<COMMON>/worktrees/` read-only
with only the checkout's own admin dir writable, so a `git worktree add` into `<COMMON>/worktrees/<snap>`
fails under both `ro` and ciu-`rw`): the BASELINE control (the unedited templates, flat hand-written `:rw`
bind) is the one that implicates or clears the mount mode.

Where to read it:

```bash
LOG=<log_path from --json>                       # also printed in the run header
grep -n -i "verdict\|GIT_FAILED\|Read-only file system\|fatal:\|could not create directory\|CIU-215" "$LOG"
```

The final line to quote is `run-gate: lane ... verdict <X>`; the last verdict line wins (job-check rule).

---

## 4. Run A: netcup-api-filter

### Facts used (read from the repos 2026-11-18)

- Stack: `tooling/test-runner`. Stack config source: `tooling/test-runner/ciu.defaults.toml.j2` (rendered into
  gitignored `ciu.toml`; edit the `.j2`, never the rendered file). Stack root key: `test_runner`
  (`[test_runner]`, `[test_runner.run_gate]`, ...); so the table is `[test_runner.linked_worktree]`
  (CONSUMERS 5f, CONFIG.md "`[<root>.linked_worktree]`", validator `config_model.validate_linked_worktree_table`
  reads `merged[root_key]["linked_worktree"]`).
- Hand-written block: `tooling/test-runner/ciu.compose.yml.j2`, the `{% if '/.worktrees/' in ... %}` ... `{% endif %}`
  block with the `:rw` source `${PHYSICAL_REPO_ROOT...}/../../.git`.
- Runners: persistent `py311` and `py314`, container names
  `<project_name>-<environment_tag>-test-runner-py311` / `-py314` (`naf-<INSTANCE_ID>-test-runner-py314`);
  `restart: unless-stopped`; `working_dir` = the logical checkout path; no `[ciu.worktree].up` is declared, so
  do NOT use `ciu worktree up` (it would start the default full deploy set); bring the runners up with
  `ciu up --dir tooling/test-runner` (this checkout's own runners are the precondition of every lane,
  `docs/TESTING_INFRASTRUCTURE.md`).
- Lane: run-gate lane **`recovery_codes`** (`run-gate.toml`: `kind = "assay"`, `environment =
  "test-runner-py314"`, `assay_lane = "recovery_codes"`), assay lane `[lanes.recovery_codes]` with
  `[lanes.recovery_codes.isolation] snapshot_selection = "repository"` (`assay.toml` ~199-210), R0+R1 whole
  target, budget 30m. All four whole-target lanes (`token_auth`, `account_auth`, `recovery_codes`, `utils`)
  run the full `tests` tree under coverage; `recovery_codes` is the smallest coverage target (76 lines, 12
  branches). The lane needs no `--base`. Do not use the `_r2` lanes (8h budget, ephemeral runner).
- Env order (docs): `ciu env print`, `PWMCP_NETWORK`, `.env.guard`, then `.env.services`. `.env.services` is
  tracked in git (so a fresh worktree has it); `.env.guard` is gitignored: source it from the main checkout,
  never copy it. `PWMCP_NETWORK` is derived with the `sed` line verbatim from `docs/DEV_INFRA_SETUP.md:59`.
  These exports live ONLY in the A3/A4 calls (rule 7).

### The edit (a unified diff against `netcup-api-filter` main, 2026-11-18)

`git apply --check` passed read-only against `/workspaces/netcup-api-filter` on 2026-11-18. Extracted to
`/tmp/ciu215-naf.patch` by section 2 step 4; apply it in the SCRATCH worktree only.

```diff
--- a/tooling/test-runner/ciu.compose.yml.j2
+++ b/tooling/test-runner/ciu.compose.yml.j2
@@ -47,32 +47,6 @@
       - type: volume
         source: run-gate-state-{{ v.name }}
         target: {{ test_runner.run_gate.state_root }}
-{% if '/.worktrees/' in ciu.repo_root|string %}
-      # LINKED-WORKTREE INSTANCE ONLY (renders only when repo_root names a
-      # `.worktrees/<name>` checkout): the git COMMON directory, read-write.
-      #
-      # Every checkout runs its own runners (environment_tag = $INSTANCE_ID), and
-      # a worktree's runner mounts only the worktree subtree above. The worktree's
-      # `.git` is a pointer file naming `<main checkout>/.git/worktrees/<name>`, a
-      # path that mount does not carry. Without this block every git call in the
-      # runner exits 128: assay lanes abort with ERROR/GIT_FAILED before any lane
-      # work, and the tracked-file hygiene tests silently fall back to a
-      # filesystem walk (tests/test_powerdns_config_hygiene.py).
-      #
-      # Both halves are DERIVED, never configured: the host path is
-      # PHYSICAL_REPO_ROOT two levels up (naf's layout is `.worktrees/<name>`),
-      # the container path is ciu.repo_root cut at `/.worktrees/`, which is
-      # exactly what the pointer file names.
-      #
-      # rw on purpose: assay lanes with [lanes.*.isolation] snapshot_selection =
-      # "repository" (assay.toml) build their snapshot with `git worktree add`,
-      # which creates `<common dir>/worktrees/<snap>`; `:ro` turns that into a
-      # bare GIT_FAILED. Parity, not a new privilege: the main checkout's runner
-      # mounts the whole checkout, `.git` included, read-write. A lane that
-      # abandons a snapshot leaves an entry `git worktree prune` clears.
-      # Reference consumer: dstdns tools/test-runner/ciu.compose.yml.j2.
-      - ${PHYSICAL_REPO_ROOT:?required host path — source ciu.env}/../../.git:{{ (ciu.repo_root|string).split('/.worktrees/')[0] }}/.git:rw
-{% endif %}
     # The suite opens many SQLite files and sockets in one process; Docker's per-process default of 1024 is
     # too low for it. A need, not a host limit.
     ulimits:
--- a/tooling/test-runner/ciu.defaults.toml.j2
+++ b/tooling/test-runner/ciu.defaults.toml.j2
@@ -26,6 +26,10 @@
 python_version = "{{ build.python_version_secondary }}"
 python_base_image = "{{ build.python_base_image_secondary }}"
 
+# CIU-215 probe 10 (SCRATCH worktree only, never committed): ciu mounts the git common dir.
+[test_runner.linked_worktree]
+git_common_dir = "ro"
+
 [test_runner.run_gate]
 # MUST equal run-gate.toml [environments.*].state_root (outside every checkout).
 state_root = "/run-gate-state"
```

If `git apply --check` fails in step A2 because the consumer's main moved, re-derive the same two edits by
hand with the Edit tool in the scratch worktree (delete the `{% if '/.worktrees/' ... %}`...`{% endif %}` block;
insert the three-line table before `[test_runner.run_gate]`), and note the drift in the results table.

### Steps

```bash
# A0+A1 (ONE call; fresh shell that has NOT sourced A3). Window agreed with the controller first.
source /tmp/ciu215-probe.env
cd /workspaces/netcup-api-filter                       # MAIN checkout: only used to CREATE and REMOVE the scratch worktree
git status --short | head -3                           # informational; do not touch anything shown
echo 'export NAME=ciu215-probe-naf' >> /tmp/ciu215-probe.env
echo 'export WT=/workspaces/netcup-api-filter/.worktrees/ciu215-probe-naf' >> /tmp/ciu215-probe.env
source /tmp/ciu215-probe.env; test -n "$NAME" && test -n "$WT"
ciu worktree create "$NAME" --base main                # WITHOUT --up
ciu worktree inspect "$NAME" --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d)'   # read the checkout path, branch and INSTANCE_ID
test -f "$WT/.git" && cat "$WT/.git"                   # PASS: one line, gitdir: <main>/.git/worktrees/<name>
#   branch name = $NAME and path = <primary>/.worktrees/<NAME> (audit-verified in worktree.py); the inspect output must agree, else stop

# A2  the edit, scratch only (own call)
source /tmp/ciu215-probe.env; test -n "$WT"
cd "$WT"
git apply --check /tmp/ciu215-naf.patch && git apply /tmp/ciu215-naf.patch
git status --short                                      # PASS: exactly the two .j2 files modified, nothing else
grep -c "linked_worktree" tooling/test-runner/ciu.defaults.toml.j2          # PASS: 1
grep -c "worktrees/' in ciu.repo_root" tooling/test-runner/ciu.compose.yml.j2   # PASS: 0

# A3 + A3b (ONE call, its own; the only place besides A4 that sources the guard env. Nothing from it is written to the env file)
source /tmp/ciu215-probe.env; test -n "$WT"; cd "$WT"
eval "$(ciu env print)"
export PWMCP_NETWORK="$(sed -n 's/^network_name = "\(.*\)"$/\1/p' /srv/pwmcp-instance/ciu.global.toml)"
test -n "$PWMCP_NETWORK" && echo pwmcp-network-ok
set -a; . /workspaces/netcup-api-filter/.env.guard; . ./.env.services; set +a
# A3b  fragment preview WITHOUT containers. `ciu render` has no --dir (it takes --profile --phases --root-folder --host).
#      engine.py writes the fragment and prints the INFO line (~2960-2985) BEFORE the `if dry_run:` return (~3020), so:
ciu up --dir tooling/test-runner --dry-run 2>&1 | grep -n "CIU-215\|ERROR\|refus"      # UNVERIFIED (code reading only); if it errors for a reason unrelated to CIU-215, skip A3b and rely on A4
ls -l tooling/test-runner/.ciu/ciu.compose.gitdir.yml                                   # UNVERIFIED as above
```

**A3b expected:** `[INFO] [CIU-215] git common dir mounted (ro) via .ciu/ciu.compose.gitdir.yml` and the file
exists. **Fail:** a `[CIU-215] ... refuses` message (copy it verbatim). A3b is optional; A4 is the binding check.

```bash
# A4  bring up this checkout's own runners: a 10-25 min build, so DETACHED, exit code kept in the log. One call;
#     it needs the A3 environment, so repeat the A3 sourcing here; nothing else runs meanwhile.
source /tmp/ciu215-probe.env; test -n "$WT"; cd "$WT"
eval "$(ciu env print)"
export PWMCP_NETWORK="$(sed -n 's/^network_name = "\(.*\)"$/\1/p' /srv/pwmcp-instance/ciu.global.toml)"
set -a; . /workspaces/netcup-api-filter/.env.guard; . ./.env.services; set +a
setsid nohup bash -c 'python3 -m ciu up --dir tooling/test-runner; echo "rc=$?"' > /tmp/ciu215-naf-up.log 2>&1 < /dev/null &
echo $! > /tmp/ciu215-naf-up.pid
```

```bash
# A4b  wait and read in SEPARATE calls (no guard env needed): until the log has an `rc=` line
source /tmp/ciu215-probe.env; test -n "$WT"; cd "$WT"
tail -n 3 /tmp/ciu215-naf-up.log; kill -0 "$(cat /tmp/ciu215-naf-up.pid)" 2>&1 | head -1    # "No such process" = finished
grep -n "^rc=" /tmp/ciu215-naf-up.log                                  # PASS: rc=0 (anything else: stop, capture, section 7)
grep -n "CIU-215\|ERROR\|\[WARN\]" /tmp/ciu215-naf-up.log
grep -c "git common dir mounted (ro)" /tmp/ciu215-naf-up.log          # PASS: 1
ls -l tooling/test-runner/.ciu/ciu.compose.gitdir.yml                  # PASS: exists
grep -n "read_only: true" tooling/test-runner/.ciu/ciu.compose.gitdir.yml | head -3   # PASS: the common dir bind is read_only

# A5  the exact runner name, from run-gate itself (header line `container <name>`)
run-gate recovery_codes --worktree "$PWD" --dry-run 2>&1 | grep -n "container"
echo 'export RUNNER=<the py314 container name from that line>' >> /tmp/ciu215-probe.env    # expect: naf-<INSTANCE_ID>-test-runner-py314 (type the real name)
source /tmp/ciu215-probe.env; test -n "$RUNNER"
docker inspect --type container "$RUNNER" --format '{{.State.Status}}'     # PASS: running
docker image inspect --format '{{.Id}}' netcup-api-filter/test-runner:py314   # record: the shared tag moved (section 0a)
```

### Checks (a)-(c) inside the runner (py314), exact expected output

`COMMON` below = the directory named by the gitfile minus `/worktrees/<name>`: read it, never guess.

```bash
source /tmp/ciu215-probe.env; test -n "$RUNNER" && test -n "$WT"          # one call for all rows below; no guard env
WTIN=$(docker exec "$RUNNER" pwd)                                       # = the logical checkout path
docker exec "$RUNNER" cat "$WTIN/.git"                                  # gitdir: <COMMON>/worktrees/<name>
COMMON=$(docker exec "$RUNNER" sh -c 'sed -e "s#^gitdir: ##" -e "s#/worktrees/.*##" .git')
echo "$COMMON"
```

| id | command | PASS | FAIL |
|---|---|---|---|
| a | `docker exec "$RUNNER" git rev-parse HEAD; echo "rc=$?"` | one 40-hex line equal to `git -C "$WT" rev-parse HEAD` on the venue, then `rc=0` | `fatal: not a git repository ...` / `fatal: detected dubious ownership` (rc=128). Dubious ownership is a uid mismatch, not the mount: record it, retry once with `-e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*'` (dstdns does the same) and mark the retry in the table |
| b | `docker exec "$RUNNER" git status; echo "rc=$?"` | output begins `On branch ciu215-probe-naf` (or the scratch branch name from `inspect`), no line containing `fatal:`, `error:` or `warning:`, then `rc=0`. The two edited `.j2` files appear as modified; that is expected | any `fatal:`/`error:` line or `rc` other than 0 |
| c | `docker exec "$RUNNER" git -c user.name=probe -c user.email=probe@invalid commit --allow-empty -m probe; echo "rc=$?"` | `fatal: Unable to create '<COMMON>/worktrees/<name>/index.lock': Read-only file system` and `rc=128`. (The identity flags keep "Author identity unknown" from masking the lock error.) | `rc=0` (the mount is writable: wrong mode) or a different error such as `Permission denied` (not EROFS: record verbatim) |
| c2 | `git -C "$WT" log --oneline -1` on the venue | unchanged: no `probe` commit | a `probe` commit exists |
| mount | `docker inspect --type container "$RUNNER" --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}} rw={{.RW}}{{"\n"}}{{end}}' \| grep -n "\.git"` | a bind whose destination is `<COMMON>` with `rw=false`; a bind of `<WTIN>/.git` with `rw=false`; sanitized `config` overlays `rw=false`; NO bind of `.../../.git` with `rw=true` | an `rw=true` bind of the common dir (the hand-written block is still rendered: recheck A2) |

Informational (record, no verdict): `docker exec "$RUNNER" sh -c ': > "$0/probe-write"' "$COMMON"` should print
`Read-only file system`.

### (d) The lane

```bash
# A6  launch detached (a lane outlasts the foreground cap), from the scratch worktree; no guard env needed
source /tmp/ciu215-probe.env; test -n "$WT"; cd "$WT"
setsid nohup run-gate recovery_codes --worktree "$PWD" --json > /tmp/ciu215-naf-lane.json 2> /tmp/ciu215-naf-lane.err < /dev/null &
echo $! > /tmp/ciu215-naf-lane.pid
# wait in separate calls: tail -n 3 /tmp/ciu215-naf-lane.err ; kill -0 "$(cat /tmp/ciu215-naf-lane.pid)"
# (UNVERIFIED: that run-gate needs none of the .env.services forward_env values for this lane; recovery_codes declares no required_env)
```

```bash
# A7  read the verdict (separate step)
source /tmp/ciu215-probe.env
python3 -c 'import json; d=json.load(open("/tmp/ciu215-naf-lane.json")); print({k: d.get(k) for k in ("verdict","exit_code","reason","log_path")}); print(d)' | head -40
LOG=<log_path>; grep -n -i "verdict\|GIT_FAILED\|Read-only file system\|fatal:\|could not create directory\|CIU-215" "$LOG"
```

Reading: apply the table in section 3. For this lane a test/coverage FAIL is probe PASS; only
`GIT_FAILED` / EROFS text fails the probe. Quote the final `run-gate: lane ... verdict ...` line.

### A8 rw sub-check (design note test 10: "check the `rw` layout once with in-lane commits")

Do this once, after A7, in the same scratch worktree. Hand-edit `git_common_dir = "ro"` to `"rw"` in
`tooling/test-runner/ciu.defaults.toml.j2` (Edit tool, one word), then recreate only the runners:

```bash
# A8a  recreate the runners under rw: the A4 launch block (A3 sourcing, detached `python3 -m ciu up --dir tooling/test-runner`
#      writing /tmp/ciu215-naf-up.log with a final rc= line), preceded by `python3 -m ciu down --dir tooling/test-runner`
#      in the same call. Read it as A4b; PASS: rc=0 and "git common dir mounted (rw)".
```

```bash
# A8b  checks, one call, no guard env
source /tmp/ciu215-probe.env; test -n "$RUNNER" && test -n "$WT"
# repeat checks a and b unchanged (PASS as above), then:
docker exec "$RUNNER" git -c user.name=probe -c user.email=probe@invalid commit --allow-empty -m probe; echo "rc=$?"   # PASS: a commit line and rc=0
git -C "$WT" log --oneline -1                                       # shows the probe commit: it is on the scratch branch only
# informational worktree-add probe, GATED: only when the rw layout is really in place, i.e. a `<COMMON>/worktrees` bind
# with rw=false exists. If that bind is missing or rw, SKIP it: git worktree add would write into the consumer's MAIN .git.
docker inspect --type container "$RUNNER" --format '{{range .Mounts}}{{.Destination}} rw={{.RW}}{{"\n"}}{{end}}' | grep -n "/worktrees "   # expect: <COMMON>/worktrees rw=false
#   only if that line shows rw=false:
docker exec "$RUNNER" git -c user.name=probe -c user.email=probe@invalid worktree add --detach /tmp/probe-wt HEAD; echo "rc=$?"   # PASS (informational): refuses with Read-only file system
docker exec "$RUNNER" git worktree remove --force /tmp/probe-wt 2>&1 | head -2      # no-op when the add was refused; mandatory if it succeeded
git -C /workspaces/netcup-api-filter worktree list | grep -c "probe-wt"             # PASS: 0 (main checkout shows no probe-wt)
```

Under `rw` the lane is NOT re-run for Run A unless A7 failed with GIT_FAILED (then follow section 6a). The
recreate may rebuild nothing: the image tag is unchanged.

### A9 cleanup (always, even after a failure)

A plain `ciu worktree rm "$NAME" --yes` REFUSES here: the scratch checkout has uncommitted edits (the two `.j2`
files), protected ignored state (`.assay/`, `vol-*`) and, after the rw sub-check, an unmerged branch tip. So
dry-run first, read the blockers, abort on any surprise, then remove with the three exact-branch consents.

```bash
# A9a  fresh call, NOT the A3 environment. Record the exact names, then read the dry-run.
source /tmp/ciu215-probe.env; test -n "$NAME" && test -n "$RUNNER"
cd /workspaces/netcup-api-filter                       # main checkout; the remover cannot be inside the checkout it removes
docker inspect --type container "$RUNNER" --format '{{.Name}}'                              # exact py314 name
PY311="${RUNNER%-py314}-py311"; docker inspect --type container "$PY311" --format '{{.Name}}'   # exact py311 name (same prefix, template naming)
docker inspect --type container "$RUNNER" --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\n"}}{{end}}{{end}}'   # exact volume name(s)
ciu worktree rm "$NAME" --dry-run                      # READ the output
```

**Abort rule.** The dry-run may list ONLY: the two modified `.j2` files, `.assay/`, and rendered `.ciu`
artifacts (`.ciu/`, `.ciu/gitdir/`, rendered `ciu.toml` / `ciu.compose.yml` / `ciu.compose.gitdir.yml`), plus
`vol-*` protected state and the unmerged scratch branch. If it lists anything else (a path outside those, a
second branch, a mount under the checkout, a refusal that is not a consent), STOP, capture it, and ask the
controller; do not widen the consents.

```bash
# A9b  only after the dry-run passed the abort rule
source /tmp/ciu215-probe.env; test -n "$NAME"
cd /workspaces/netcup-api-filter
ciu worktree rm "$NAME" --yes --discard-changes "$NAME" --discard-protected-state "$NAME" --delete-branch
#   consent values = the EXACT branch name = $NAME (UNVERIFIED beyond the audit's reading of worktree.py create()/remove(): the
#   dry-run output and `ciu worktree rm --help` must agree; if the refusal names another branch string, use that exact string)
ciu worktree list                                       # PASS: no "$NAME"
git worktree list | grep -c "$NAME"                     # PASS: 0
git branch --list "$NAME*"                              # PASS: empty (--delete-branch removed it; no manual branch -D)
docker inspect --type container "$RUNNER"              # PASS: "No such object"
docker inspect --type container "${RUNNER%-py314}-py311"   # PASS: "No such object"
docker volume inspect <each exact volume name recorded in A9a>   # PASS: "No such volume" (if one remains: docker volume rm <that exact name>)
```

If a container still exists after `ciu worktree rm`, remove it by exact name only (`docker rm -f <exact name>`);
a checkout whose removal fails with `Permission denied` is reported with its path, not deleted by hand
(dstdns GUIDE 3.3).

---

## 5. Run B: dstdns

Run B starts only after A9 has passed and the controller has confirmed Run A's end.

### Facts used (read from the repos 2026-11-18)

- Stack: `tools/test-runner`; stack config source `tools/test-runner/ciu.defaults.toml.j2`; root key
  `test_runner`; table `[test_runner.linked_worktree]`.
- Hand-written block: `tools/test-runner/ciu.compose.yml.j2`, the `{% if '/.worktrees/' ... %}` block with its
  long comment (it records the `:ro` measurement) and the `:rw` bind `${PHYSICAL_REPO_ROOT...}/../../.git`.
- One runner service `test-runner`, container `<project_name>-<environment_tag>-test-runner`, image
  `dstdns/test-runner:local` (shared, already built; no rebuild). It needs vault secrets
  (`ASK_VAULT`), so the worktree's OWN `core` + `db` stacks must be up first. `[ciu.worktree].up` declares
  `core`, `db`, `runner`, so `ciu worktree up <name>` starts all three in the right order (GUIDE 3.4).
  **This is heavy** (vault, postgres, redis, two runners): it is the expensive part of Run B.
- Lane: run-gate lane **`p128_persister_lineage`** (`run-gate.toml` ~833: `kind = "assay"`, `assay_lane =
  "p128_persister_lineage"`, budget 10m; environment = the `[assay]` default `test-runner`); assay lane
  `assay.toml` ~239: `snapshot_selection = "repository"`, `mode = "changed_lines"`, `base_source = "request"`,
  argv `bash scripts/p128-assay-schema.sh`. Consequences: (1) it needs an explicit `--base`, otherwise
  `NOT_RUN no-base` (INCONCLUSIVE, `docs/RUN-GATE.md` line 88); (2) the script starts a disposable PostgreSQL
  and a disposable runner clone as extra containers of this instance (a second and third container while the
  lane runs: still one lane at a time), and replicates the runner's mounts from `docker inspect`
  (`scripts/assay_schema_runtime.sh`), so it exercises the CIU-215 binds a second time.
- Do not run `p128-schema-lineage` (the `kind = "command"` companion): it does not build an assay snapshot and
  cannot settle the question.

### The edit (a unified diff against `dstdns` main, 2026-11-18)

`git apply --check` passed read-only against `/workspaces/dstdns` on 2026-11-18. Extracted to `/tmp/ciu215-dst.patch` by section 2 step 4.

```diff
--- a/tools/test-runner/ciu.compose.yml.j2
+++ b/tools/test-runner/ciu.compose.yml.j2
@@ -93,47 +93,6 @@
       - type: volume
         source: run-gate-state
         target: {{ test_runner.run_gate.state_root }}
-{% if '/.worktrees/' in ciu.repo_root|string %}
-      # LINKED-WORKTREE INSTANCE ONLY (this block renders only when repo_root
-      # names a worktree): the git COMMON directory, read-only.
-      #
-      # A linked worktree's `.git` is a POINTER FILE holding the ABSOLUTE path
-      # of a directory inside the MAIN checkout's `.git` — a path the workspace
-      # mount above does not carry, because that mount is the worktree subtree
-      # alone. Without this, everything in the container that shells out to git
-      # is structurally broken, not merely degraded: `git rev-parse
-      # --absolute-git-dir` exits 128, the assay lanes abort with
-      # ERROR/GIT_FAILED (so `run-gate gate` cannot run at all against a Mode-B
-      # instance), and every git-backed sweep test SKIPS while the lane still
-      # exits 0 — a false green that the exit code does not show.
-      #
-      # Both halves are DERIVED, never configured (AGENTS.md §4.2a): the host
-      # path is PHYSICAL_REPO_ROOT truncated at `/.worktrees/`, the container
-      # path is `ciu.repo_root` truncated the same way, which is exactly what
-      # the pointer file names.
-      #
-      # READ-WRITE, deliberately, and this is the whole of the reason: an assay
-      # lane declaring `[lanes.<x>.isolation] snapshot_selection = "repository"`
-      # builds its snapshot with `git worktree add`, which CREATES
-      # `<git-common-dir>/worktrees/<snap>`. Mounted `:ro` that is
-      # `fatal: could not create directory of '.../.git/worktrees/<snap>':
-      # Read-only file system`, surfacing as a bare ERROR/GIT_FAILED whose
-      # message assay does not propagate -- measured on
-      # `[lanes.p128_persister_lineage]`. `:ro` is therefore not the safe
-      # choice, only the quieter one: it trades a loud missing mount for a
-      # silent lane that cannot run.
-      #
-      # This grants no privilege main's runner does not already have: the MAIN
-      # checkout's own test-runner mounts the entire repo root -- `.git`
-      # included -- read-write, so a worktree-owned instance reaching the same object
-      # store read-write is parity with main's runner, not a new risk class.
-      #
-      # Shortcomings: a gate CAN now write to the main checkout's object store.
-      # In practice what it writes is `.git/worktrees/<snap>` bookkeeping that
-      # assay removes; a lane that abandons a snapshot leaves an entry
-      # `git worktree prune` clears from the main checkout.
-      - ${PHYSICAL_REPO_ROOT:?required host path — ciu seeds it from ciu.instance.generated.toml; run `ciu env generate`}/../../.git:{{ (ciu.repo_root|string).split('/.worktrees/')[0] }}/.git:rw
-{% endif %}
     working_dir: {{ ciu.repo_root }}
     stdin_open: true
     tty: true
--- a/tools/test-runner/ciu.defaults.toml.j2
+++ b/tools/test-runner/ciu.defaults.toml.j2
@@ -16,6 +16,10 @@
 ]
 name = "test-runner"
 
+# CIU-215 probe 10 (SCRATCH worktree only, never committed): ciu mounts the git common dir.
+[test_runner.linked_worktree]
+git_common_dir = "ro"
+
 [test_runner.run_gate]
 # run-gate's durable state root inside this runner. MUST equal run-gate.toml
 # [environments.test-runner].state_root and lie outside every checkout path
```

(The recorded measurement the diff deletes is quoted in the design note: `fatal: could not create directory of
'.../.git/worktrees/<snap>': Read-only file system`. That exact string is what to grep for in B7.)

### Steps

```bash
# B0+B1 (ONE call; fresh shell that has NOT sourced A3 or any guard env; Run A is cleaned up and the window confirmed)
source /tmp/ciu215-probe.env
test -n "$CGROUP_PARENT_DEV_BACKGROUND" && echo cgroup-parent-set      # p128 script hard-requires it (`:?`)
cd /workspaces/dstdns
echo 'export NAME=ciu215-probe-dst' >> /tmp/ciu215-probe.env
echo 'export WT=/workspaces/dstdns/.worktrees/ciu215-probe-dst' >> /tmp/ciu215-probe.env
sed -i '/^export RUNNER=/d' /tmp/ciu215-probe.env      # drop Run A's runner name (the env file is the runbook's own scratch file)
source /tmp/ciu215-probe.env; test -n "$NAME" && test -n "$WT"
ciu worktree create "$NAME" --base main                # WITHOUT --up (UNVERIFIED flags: `ciu worktree create --help`)
ciu worktree inspect "$NAME" --json | python3 -c 'import json,sys; print(json.load(sys.stdin))'
cat "$WT/.git"                                          # PASS: gitdir: <main>/.git/worktrees/<name>; inspect must agree on path and branch

# B2  the edit, scratch only (own call)
source /tmp/ciu215-probe.env; test -n "$WT"
cd "$WT"
git apply --check /tmp/ciu215-dst.patch && git apply /tmp/ciu215-dst.patch
git status --short                                       # PASS: exactly the two .j2 files modified
grep -c "linked_worktree" tools/test-runner/ciu.defaults.toml.j2                   # PASS: 1
grep -c "worktrees/' in ciu.repo_root" tools/test-runner/ciu.compose.yml.j2        # PASS: 0

# B3  start the declared environment (core, db, runner, and the ui runner) from the EDITED templates; detached, it is long.
#     ALWAYS through `ciu worktree up` (dstdns GUIDE 3.4 forbids hand-run `ciu render` / `ciu up --dir tools/test-runner --dry-run`
#     in a not-yet-rendered worktree); no guard env is needed or allowed here.
source /tmp/ciu215-probe.env; test -n "$NAME"
setsid nohup bash -c 'python3 -m ciu worktree up "$0"; echo "rc=$?"' "$NAME" > /tmp/ciu215-dst-up.log 2>&1 < /dev/null &
echo $! > /tmp/ciu215-dst-up.pid
#     UNVERIFIED: `ciu worktree up` renders from the checkout's working files (uncommitted edits). The proof is B4's INFO line.
```

```bash
# B3b/B4  separate calls: wait for the `rc=` line, then read
source /tmp/ciu215-probe.env; test -n "$WT"; cd "$WT"
tail -n 3 /tmp/ciu215-dst-up.log; kill -0 "$(cat /tmp/ciu215-dst-up.pid)" 2>&1 | head -1
grep -n "^rc=" /tmp/ciu215-dst-up.log                   # PASS: rc=0
grep -n "CIU-215" /tmp/ciu215-dst-up.log                # PASS: "[INFO] [CIU-215] git common dir mounted (ro) via .ciu/ciu.compose.gitdir.yml"
ls -l tools/test-runner/.ciu/ciu.compose.gitdir.yml     # PASS: exists
# the lane base (MEDIUM-6): the PARENT of the last commit that touched libs/common/src, so changed lines exist
LASTC=$(git log -n 1 --format=%H -- libs/common/src); test -n "$LASTC"
BASE=$(git rev-parse "$LASTC^"); test -n "$BASE"
echo "export BASE=$BASE" >> /tmp/ciu215-probe.env
git diff --stat "$BASE" HEAD -- libs/common/src | tail -3     # PASS: at least one .py file changed (else pick an older base; never HEAD~1 blindly)
run-gate p128_persister_lineage --worktree "$PWD" --base "$BASE" --dry-run 2>&1 | grep -n "container"    # flag order is irrelevant for run-gate (audit)
echo 'export RUNNER=<container name from that line>' >> /tmp/ciu215-probe.env       # expect: dstdns-<INSTANCE_ID>-test-runner (type the real name)
source /tmp/ciu215-probe.env; test -n "$RUNNER"
docker inspect --type container "$RUNNER" --format '{{.State.Status}}'      # PASS: running
```

If `[CIU-215]` is missing from `/tmp/ciu215-dst-up.log` because `worktree up` re-ran an already-rendered
runner without recreating it, the runner kept its old (no-mount) config: `docker inspect` then shows no
common-dir bind and the checks are meaningless. Stop and ask the controller how to force a recreate (UNVERIFIED
which verb does; `ciu worktree up --help` first). Do not hand-run `ciu up --dir tools/test-runner --dry-run`
or `ciu render` in a not-yet-rendered worktree (GUIDE 3.4); by this point the worktree IS rendered, so a
plain `ciu up --dir tools/test-runner` from `$WT` (with `PYTHONPATH` exported) is the fallback the controller
may approve.

### Checks (a)-(c)

Identical to Run A (same table, same expected output and same `c2`/`mount` rows), with `$RUNNER` the dstdns
runner, the branch name from `ciu worktree inspect`, and the dstdns note that its image may need the
`safe.directory` env (`GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'`): use it only
as the documented retry in row (a), and record that it was needed.

### (d) The lane

```bash
# B6  detach; --base is required by the lane (changed_lines, base_source = request). $BASE (written in B4) is the parent
#     of the last commit touching libs/common/src, so the lane has changed lines to judge; HEAD~1 could touch no Python
#     there and give NO_MEASUREMENT / INCONCLUSIVE without ever running the script (a false PASS).
source /tmp/ciu215-probe.env; test -n "$WT" && test -n "$BASE" && test -n "$RUNNER"; cd "$WT"
setsid nohup run-gate p128_persister_lineage --worktree "$PWD" --base "$BASE" --json > /tmp/ciu215-dst-lane.json 2> /tmp/ciu215-dst-lane.err < /dev/null &
echo $! > /tmp/ciu215-dst-lane.pid
# wait in separate calls: tail -n 3 /tmp/ciu215-dst-lane.err ; kill -0 "$(cat /tmp/ciu215-dst-lane.pid)"
```

```bash
# B7  read separately, as in A7 (section 3 rules)
source /tmp/ciu215-probe.env
python3 -c 'import json; d=json.load(open("/tmp/ciu215-dst-lane.json")); print({k: d.get(k) for k in ("verdict","exit_code","reason","log_path")}); print(d)' | head -40
LOG=<log_path>
grep -n -i "verdict\|GIT_FAILED\|Read-only file system\|fatal:\|could not create directory\|Permission denied\|CIU-215" "$LOG"
grep -n "schema-p128" "$LOG"          # REQUIRED evidence that scripts/p128-assay-schema.sh ran (the `<prefix>schema-p128-<pid>` container line)
```

**Evidence rule for Run B.** A verdict counts toward `ro confirmed` only if the log also shows the script ran
(the `schema-p128` container line). A verdict without it (for example `NO_MEASUREMENT` because the base touched
no changed lines, or a lane that stopped before the script) is INCONCLUSIVE: choose an older `$BASE` and rerun.

**What each outcome means for the open question.**

| B7 result | reading |
|---|---|
| verdict reached (any of PASS/FAIL by test or coverage), no `could not create directory` / `Read-only file system` text | **`ro` confirmed for dstdns**: the 2026-09-03 measurement is not reproducible on the current pinned judge (`tools/assay/assay-12.0.0.pyz`). Evidence to quote: the final verdict line, the INFO line from B4, rows a-c, the `mount` row |
| `ERROR/GIT_FAILED` and the log contains `fatal: could not create directory of '.../.git/worktrees/...': Read-only file system` | **`ro` implicated, not yet refuted for good**: the mount mode is a candidate cause. Run the controls of section 6a (baseline FIRST) before any recommendation |
| `ERROR/GIT_FAILED` with a message about removing/cleaning the snapshot, `Permission denied`, root-owned files | probably not the mount (dstdns FU-5): run the baseline control (section 6a); if the baseline fails the same way, record "GIT_FAILED independent of mount mode" and the dstdns probe is INCONCLUSIVE, neither a pass nor a refutation of `ro` |
| `ERROR/GIT_FAILED` with no message | run the controls of section 6a; compare the logs line by line |

### B8 in-lane-commit rw sub-check (design note test 10) and the controls

The in-lane-commit sub-check is done as A8 (set `"rw"` in `tools/test-runner/ciu.defaults.toml.j2`, recreate the
runner through `ciu worktree up "$NAME"` or the controller-approved fallback of B4, confirm the INFO line says
`(rw)`, repeat checks a and b, run the in-runner commit: PASS `rc=0`, a commit line, on the scratch branch only;
the gated `git worktree add` probe of A8b applies unchanged, with the dstdns main checkout in the last line).
When B7 was GIT_FAILED, the controls of section 6a are mandatory and replace "repeat under rw" on its own.

### B9 cleanup (always)

As in A9: a plain `rm --yes` refuses (uncommitted `.j2` edits, `.assay/` and `vol-*` protected state, an
unmerged branch tip), so dry-run, read, abort on surprises, then remove with the exact-branch consents.

```bash
# B9a  fresh call, NOT the guard env
source /tmp/ciu215-probe.env; test -n "$NAME" && test -n "$RUNNER"
cd /workspaces/dstdns
docker inspect --type container "$RUNNER" --format '{{.Name}}'      # exact runner name; record also the exact ui runner name (<prefix>-test-runner-ui, from `ciu worktree inspect`/the up log; never an inventory)
docker inspect --type container "$RUNNER" --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\n"}}{{end}}{{end}}'   # exact volume name(s)
ciu worktree rm "$NAME" --dry-run                        # READ the output; same abort rule as A9 (only the two .j2 files, .assay/, .ciu artifacts, vol-*, the unmerged branch)
```

```bash
# B9b  only after the dry-run passed the abort rule
source /tmp/ciu215-probe.env; test -n "$NAME"
cd /workspaces/dstdns
ciu worktree rm "$NAME" --yes --discard-changes "$NAME" --discard-protected-state "$NAME" --delete-branch   # ciu clean under that instance (also removes leftover disposable p128 containers, which carry the instance prefix), then the checkout and the branch
ciu worktree list; git worktree list | grep -c "$NAME"  # PASS: not listed / 0
git branch --list "$NAME*"                               # PASS: empty (no manual branch -D)
docker inspect --type container "$RUNNER"               # PASS: "No such object"; repeat for each exact name recorded in B9a (ui runner, volumes via docker volume inspect)
```

```bash
# Step Z  last call of the probe
source /tmp/ciu215-probe.env
test -n "$VBPUB" && test -n "$CIU_SRC_ROOT"
# only if section 2 step 1 created the detached vbpub source checkout (CIU_SRC_ROOT != VBPUB):
[ "$CIU_SRC_ROOT" != "$VBPUB" ] && git -C "$VBPUB" worktree remove --force "$VBPUB/.worktrees/ciu215-probe-src"
git -C /workspaces/netcup-api-filter status --short | diff - /tmp/ciu215-naf-main.status && echo naf-main-unchanged
git -C /workspaces/dstdns status --short | diff - /tmp/ciu215-dst-main.status && echo dst-main-unchanged
git -C /workspaces/netcup-api-filter log origin/main.. --oneline | head -3      # nothing pushed or committed on main
git -C /workspaces/dstdns log origin/main.. --oneline | head -3
rm -f /tmp/ciu215-naf.patch /tmp/ciu215-dst.patch /tmp/ciu215-probe.env /tmp/ciu215-*-main.status
```

(`git status --short` in a consumer main checkout lists ignored-nothing; an `info/exclude` line added by `ciu
worktree create` does not show there: read `<main>/.git/info/exclude` once if the controller wants it clean,
section 0a.)

---

## 6. Results table (fill in; paste verbatim lines, not paraphrases)

| field | Run A netcup-api-filter | Run B dstdns |
|---|---|---|
| date/time, host, venue shell | | |
| ciu source (`python3 -c 'import ciu;print(ciu.__file__)'`) | | |
| scratch worktree path, branch, INSTANCE_ID | | |
| patch applied cleanly (`git apply --check`) / drift note | | |
| `[INFO] [CIU-215] ... (ro)` line seen | | |
| runner exact name | | |
| a `rev-parse HEAD` (output, rc) | | |
| b `git status` (first line, any error line, rc) | | |
| c `git commit` (exact error line, rc) | | |
| c2 no probe commit on host | | |
| mount row (common dir ro, `.git` ro, no rw common-dir bind) | | |
| lane (run-gate name / assay lane) | `recovery_codes` | `p128_persister_lineage` (`--base $BASE`, parent of the last commit touching `libs/common/src`) |
| script-ran evidence (`schema-p128` line in the log) | n/a | |
| image ids of the shared tags before / after `ciu up` | | n/a (no rebuild) |
| baseline control (flat hand-written `:rw`, unedited templates): mount row, verdict line, cause | | |
| ciu-`rw` control: INFO line `(rw)`, verdict line, cause | | |
| lane `verdict` / `exit_code` / `reason` / assay outcome | | |
| final `run-gate: lane ... verdict` line | | |
| `GIT_FAILED` / `Read-only file system` / `could not create directory` in log (yes/no, line) | | |
| controls needed? (only after a GIT_FAILED, section 6a) | | |
| rw sub-check: a, b, commit rc=0, worktree-add refusal | | |
| probe reading (PASS / FAIL / INCONCLUSIVE) | | |
| cleanup verified (worktree list, `No such object` per name, branch gone) | | |
| wall time per step | | |

**Decision rule.** Both runs probe-PASS: CIU-215 can close (the close-when) and the design note's
recommendation stands; amend SPEC-V8 S16.4.9 / S6.2.4 to `ro` plus an opt-in `rw` with the RG-NARROW/RG-86
layout. Any run FAIL: section 7. Any run INCONCLUSIVE: repeat that run's lane step (not the whole run) after
fixing the stated cause; do not close CIU-215 or move the v8 spec on an INCONCLUSIVE.

---

## 6a. Controls after a `GIT_FAILED` under `ro` (baseline FIRST, then ciu-`rw`)

Needed only when a lane gave `ERROR/GIT_FAILED` under `ro` (Run A or Run B). Do not skip the baseline: ciu-`rw`
alone cannot discriminate (section 3a). One container-starting step at a time; the same scratch worktree, the
same lane and (Run B) the same `$BASE`. Each recreate is its own detached call with the right shell hygiene
(rule 7): Run A recreates through the A4 launch block, Run B through `ciu worktree up "$NAME"` (B3 block).

**Control 1, BASELINE (the unedited templates: the flat hand-written `:rw` bind, no table).**

```bash
source /tmp/ciu215-probe.env; test -n "$WT"; cd "$WT"
git apply -R /tmp/ciu215-naf.patch            # Run B: /tmp/ciu215-dst.patch. Restores the hand-written block, removes the table
git status --short                             # PASS: clean (the two .j2 files are back to HEAD)
```

Recreate the runner (stale fragment: ciu removes it when the table is gone, F5), then record: the INFO line is
ABSENT, `.ciu/ciu.compose.gitdir.yml` is gone, and the mount row shows the flat bind (`<COMMON>` with
`rw=true`, nothing else under `<COMMON>`). Re-run checks a and b (PASS), check c now PASSES as a commit (rc=0,
the flat bind is writable; this is the baseline, expected) and then re-run the lane (A6/A7 or B6/B7 with the
same `$BASE`).

**Control 2, ciu-`rw`.** `git apply /tmp/ciu215-*.patch`, change `"ro"` to `"rw"` in the defaults `.j2` (Edit
tool), recreate, confirm `(rw)` in the INFO line, re-run checks a, b and the lane.

**Decision table** (verdict classes per section 3: "fails" = `GIT_FAILED` with a mount-type message; "passes" =
a verdict reached with evidence the script/snapshot ran):

| `ro` | baseline | ciu-`rw` | reading and recommendation |
|---|---|---|---|
| passes | not run | not run | `ro` confirmed for this lane; keep the note's recommendation `ro` |
| fails | passes | passes | the mount mode matters but ciu's `rw` layout is enough: switch the note's recommendation to **ciu `rw`** (the RG-NARROW/RG-86 layout), keep `ro` as an opt-in for lanes that do not snapshot; SPEC-V8 gets `rw` as default |
| fails | passes | fails | **only this row** means the whole common dir must be flat `rw`: ciu's layout keeps `worktrees/` read-only and assay writes `<COMMON>/worktrees/<snap>`. Do NOT recommend ciu `rw`. Recommend either a flat-`rw` mode in ciu (a design change: new CIU entry, adversarial review) or assay no longer adding a worktree in the source (assay backlog); SPEC-V8 stays flat `rw` until decided; CIU-215 does not close |
| fails | fails | (either) | the failure is not the mount mode (FU-5 cleanup, root-owned snapshot, image, network): `ro` is NOT refuted; the probe is INCONCLUSIVE for that lane; fix or file the cause (3a) and rerun |
| fails with cause A | fails with cause B | any | two different causes: INCONCLUSIVE; record both messages verbatim; no recommendation |
| passes | fails | any | the baseline is broken (not a valid control): record and stop; the `ro` pass stands |

Leave the scratch worktree in the control's state until cleanup: A9/B9 handle uncommitted edits through the
`--discard-changes` consent.

---

## 7. If it fails

**Capture first, before cleanup** (all of it goes next to the results table; names exact):

1. The rendered fragment: `cat <stack dir>/.ciu/ciu.compose.gitdir.yml` (tooling/test-runner or tools/test-runner
   of the scratch worktree), and `ls -lR <stack dir>/.ciu/gitdir/`.
2. The runner's real mounts: `docker inspect --type container "$RUNNER" --format '{{json .Mounts}}'` and
   `... --format '{{json .HostConfig.Binds}}'`.
3. The lane log (`log_path`) and the `--json` result file; the lines matching
   `GIT_FAILED|Read-only file system|fatal:|could not create directory|Permission denied`.
4. In the runner, the exact failing git call reproduced by hand, with trace, GATED: only when the mount row
   shows NO `rw` bind of the common dir (the `ro` layout), because otherwise the add would write into the
   consumer's MAIN `.git`:
   `docker exec -e GIT_TRACE=1 "$RUNNER" git -C <logical checkout path> worktree add --detach /tmp/probe-wt HEAD`
   (shows whether the failure is `<COMMON>/worktrees/` creation, i.e. assay-style `git worktree add`). Follow
   with `docker exec "$RUNNER" git worktree remove --force /tmp/probe-wt` and check
   `git -C <consumer main checkout> worktree list | grep -c probe-wt` prints 0. Skip it under the baseline.
5. Host side: `git -C <scratch worktree> rev-parse --git-dir --git-common-dir` and `cat <scratch worktree>/.git`.
6. ciu's own refusal text if any (`[CIU-215] ...`), verbatim.

**Recommendation when a lane fails under `ro` for a mount reason: follow the decision table of section 6a, not
the `ro` failure alone.** "Switch to `rw`" is justified only by the row it names: ciu-`rw` passing where `ro`
failed (and the baseline passing). The supporting evidence, in order of strength: (1) the `ro` log line `could
not create directory of '.../.git/worktrees/<snap>': Read-only file system` from a lane whose runner mount
shows the common dir `rw=false`, with the SAME lane reaching a verdict under the baseline and under ciu-`rw`;
(2) the trace in capture item 4 showing the `git worktree add` came from assay inside the runner (that would
also make the 2026-09-03 measurement valid against the current pinned judge and the note's "cannot be
reproduced from current code" statement wrong: say so); (3) rows a, b, c passing, i.e. the mount itself worked
as specified and only the lane's own write failed. If the baseline passes but ciu-`rw` fails, the whole-common-
dir flat `rw` is what the lane needs and ciu's layout is not enough (section 6a, third row): that is a design
finding, not a recommendation to adopt ciu `rw`. In every case SPEC-V8 S16.4.9 / S6.2.4 keep their flat-`rw`
wording until the controller decides, and the assay behaviour is filed in the assay backlog
(`assay/nyxloom-trove/4-backlog.md`) if the trace shows a worktree add against the source.

**If the failure is not the mount** (3a: cleanup, root-owned snapshot, dubious ownership, network/pwmcp, a
missing image or env file): the probe is INCONCLUSIVE for that lane. Record the cause, file it in the owning
backlog (dstdns FU-5 is already filed there; a ciu defect goes to `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` as the
next CIU-NN after checking `ciu/CHANGES.md`), and do not change the `ro` recommendation.

**If ciu itself refuses** (`[CIU-215] ...`): the message names the cause (git cannot say whether the checkout
is linked, a symlinked `.ciu`, an unknown service in `services`, a shipped stack). Fix the cause named; do not
set `services` or any other key to get past it.

---

## 8. Estimated wall time

Estimates, not measurements (UNVERIFIED until the first run; the numbers below are the only ones to replace):

| step | estimate |
|---|---|
| section 1-2 preflight and ciu source check | 5-10 min |
| Run A: create + edit + env (A1-A3b) | 5-10 min |
| Run A: `ciu up --dir tooling/test-runner` (two image builds, cold cache) | 10-25 min (warm: 2-5 min) |
| Run A: checks a-c, rw sub-check | 10 min |
| Run A: lane `recovery_codes` (whole `tests` tree on py314 under coverage; budget 30m) | up to 30 min |
| Run A: cleanup | 5 min |
| Run B: create + edit (B1-B2) | 5 min |
| Run B: `ciu worktree up` (core, db, runner; vault init, postgres, redis; images shared) | 15-30 min |
| Run B: checks a-c, rw sub-check | 10 min |
| Run B: lane `p128_persister_lineage` (budget 10m; plus a disposable PostgreSQL) | 10-15 min |
| Controls (6a), only after a GIT_FAILED: baseline recreate + lane, then ciu-`rw` recreate + lane | +30-45 min |
| Run B: cleanup | 5-10 min |
| **Total** | **about 2 to 3.5 hours** of host window, strictly sequential (3 to 4.5 with the controls); ask the controller for 4-5 hours |

---

## 9. Settled by the independent audit, and what is still UNVERIFIED

**Settled (read from code or docs by the audit; not run here):**

- `PYTHONPATH` survives into `ciu worktree`'s children (they are `sys.executable -m ciu.cli ...`); the `import
  ciu` path print and the `[CIU-215]` INFO line are the checks.
- The managed worktree's branch name equals the logical name `$NAME`, and its path is
  `<primary>/.worktrees/<NAME>`; the `rm` consent values are that exact branch name.
- netcup `.env.services` is tracked in git (a fresh worktree has it); `.env.guard` is gitignored (source it from
  the main checkout). The `PWMCP_NETWORK` `sed` line is verbatim from `docs/DEV_INFRA_SETUP.md:59`.
- `run-gate` flag order is irrelevant (`--base`, `--json`, `--dry-run`).
- The EROFS wording of check (c) is `fatal: Unable to create '<COMMON>/worktrees/<name>/index.lock': Read-only
  file system`, rc 128, taken from git's source, not run here (the run is the confirmation).
- `ciu render` has no `--dir` (only `--profile --phases --root-folder --host`); the fragment and INFO line are
  written before the `if dry_run:` return in `engine.py` (~2960-2985 vs ~3020).
- ciu's `rw` layout keeps `<COMMON>/worktrees/` read-only, so `ro` and ciu-`rw` fail identically on a
  `git worktree add` into `<COMMON>/worktrees/<snap>`: only the baseline control discriminates (section 6a).

**UNVERIFIED (check each first, in this order):**

1. **Venue**: that `ciu`, `run-gate`, `docker`, `CGROUP_PARENT_DEV_BACKGROUND` and BOTH consumer checkouts
   exist in one shell on v1001 (P0), and which shell that is (host account or the cockpit container
   `mdt-cockpit-runner`, whose name comes from the private host table).
2. **vbpub source on the venue** contains `c64c2ffd8`, the interpreter has Jinja2/PyYAML/tomli_w/`cli_extended`,
   and this runbook's file is present there for the patch extraction (section 2).
3. `ciu worktree create/inspect` option and JSON field names (`ciu worktree create --help`); the exact consent
   strings `ciu worktree rm --dry-run` asks for; that the dry-run lists only the allowed blockers (A9a).
4. `ciu up --dir tooling/test-runner --dry-run` writing the fragment and printing the INFO line (A3b; code
   reading only; optional, A4 is binding).
5. The estate pwmcp instance at `/srv/pwmcp-instance` on v1001; netcup bringing up both py311 and py314 (no
   way found to start py314 only); first-build duration; that the shared image tags moving is acceptable.
6. dstdns `ciu worktree up <name>` rendering from uncommitted edits, needing nothing beyond the declared
   `core` + `db` + `runner` on this host, and how to force a runner recreate for the controls (B4, 6a).
7. That run-gate needs none of netcup's `forward_env` values for `recovery_codes`; the `--json` field names of
   the run-gate result; that `$BASE` (parent of the last `libs/common/src` commit) gives changed lines.
8. The `safe.directory` behaviour of both runner images (check a/b may need the documented retry).
9. Whether dstdns FU-5 (root-owned snapshot files) is still present with the current run-gate and assay
   (`dstdns` P171 report, measured at `84ac0b0b`); it can turn B7 into INCONCLUSIVE regardless of the mount.
10. All time estimates in section 8.
