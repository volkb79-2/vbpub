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
```

Below, `NAF=/workspaces/netcup-api-filter` and `DST=/workspaces/dstdns` (the main checkouts; adjust to the
root P0 found, consistently).

---

## 2. Prerequisite common to both runs: run the unreleased ciu, no install

The ciu with CIU-215 is on `main` (merge `c64c2ffd8`) and not released. Do not `pip install` it over the
installed ciu (devcontainer tool installs get clobbered, `which -a` first). ciu spawns itself as
`sys.executable -m ciu.cli ...` for `up`/`clean`/`env generate` (`ciu/src/ciu/worktree.py` ~2157, 4835), so one
exported `PYTHONPATH` reaches the children too.

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

# 3. shadow the installed console script for THIS shell only
ciu() { python3 -m ciu "$@"; }
export -f ciu 2>/dev/null || true
grep -c "linked_worktree" "$CIU_SRC_ROOT/ciu/src/ciu/config_model.py"      # PASS: 6 or more
python3 -m ciu worktree --help | head -3                                    # PASS: lists the worktree verbs
```

```bash
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
--base main` (no `--up`: the edit must precede the first `up`), `inspect <name> --json`, `rm <name> --yes`.

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
`rw` control (section 6 step B7): the same lane failing identically under `rw` means the mount mode is not
the cause.

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
- Env order (docs): `ciu env print`, `PWMCP_NETWORK`, `.env.guard`, then `.env.services`. A linked worktree has
  no copy of the gitignored `.env.guard` / `.env.services`; source them from the main checkout in the shell
  (never copy the guard file).

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
# A0  preconditions (one shell, venue from section 1, ciu from section 2)
cd /workspaces/netcup-api-filter                       # MAIN checkout: only used to CREATE and REMOVE the scratch worktree
git status --short | head -3                           # informational; do not touch anything shown
NAME=ciu215-probe-naf

# A1  window agreed with the controller; create the scratch worktree WITHOUT --up
ciu worktree create "$NAME" --base main
ciu worktree inspect "$NAME" --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d)'   # read the checkout path and INSTANCE_ID
WT=/workspaces/netcup-api-filter/.worktrees/$NAME      # UNVERIFIED default dir: confirm against the inspect output
test -f "$WT/.git" && cat "$WT/.git"                   # PASS: one line, gitdir: <main>/.git/worktrees/<name>

# A2  the edit, scratch only
cd "$WT"
git apply --check /tmp/ciu215-naf.patch && git apply /tmp/ciu215-naf.patch
git status --short                                      # PASS: exactly the two .j2 files modified, nothing else
grep -c "linked_worktree" tooling/test-runner/ciu.defaults.toml.j2          # PASS: 1
grep -c "worktrees/' in ciu.repo_root" tooling/test-runner/ciu.compose.yml.j2   # PASS: 0

# A3  environment, in the documented order
eval "$(ciu env print)"
export PWMCP_NETWORK="$(sed -n 's/^network_name = "\(.*\)"$/\1/p' /srv/pwmcp-instance/ciu.global.toml)"
test -n "$PWMCP_NETWORK" && echo pwmcp-network-ok
set -a; . /workspaces/netcup-api-filter/.env.guard; . ./.env.services; set +a   # UNVERIFIED: .env.services is not in a fresh worktree; if absent run the generator named in docs/CONFIGURATION_GUIDE.md / tooling/ciu_resolve_env.py first

# A3b  render-only preview, no containers (checks the table and the fragment before any build)
ciu render --dir tooling/test-runner 2>&1 | grep -n "CIU-215\|ERROR\|refus" ; ls -l tooling/test-runner/.ciu/ciu.compose.gitdir.yml   # UNVERIFIED that `ciu render --dir` writes the fragment; if it does not, A4 is the check
```

**A3b expected:** `[INFO] [CIU-215] git common dir mounted (ro) via .ciu/ciu.compose.gitdir.yml` and the file
exists. **Fail:** a `[CIU-215] ... refuses` message (copy it verbatim) or no fragment after A4.

```bash
# A4  bring up this checkout's own runners (builds two images; the longest step; one at a time, nothing else running)
ciu up --dir tooling/test-runner 2>&1 | tee /tmp/ciu215-naf-up.log | grep -n "CIU-215\|ERROR\|\[WARN\]"
grep -c "git common dir mounted (ro)" /tmp/ciu215-naf-up.log          # PASS: 1
ls -l tooling/test-runner/.ciu/ciu.compose.gitdir.yml                  # PASS: exists
grep -n "read_only: true" tooling/test-runner/.ciu/ciu.compose.gitdir.yml | head -3   # PASS: the common dir bind is read_only

# A5  the exact runner name, from run-gate itself (header line `container <name>`)
run-gate recovery_codes --worktree "$PWD" --dry-run 2>&1 | grep -n "container"
RUNNER=<the py314 container name from that line>       # expect: naf-<INSTANCE_ID>-test-runner-py314
docker inspect --type container "$RUNNER" --format '{{.State.Status}}'     # PASS: running
```

### Checks (a)-(c) inside the runner (py314), exact expected output

`COMMON` below = the directory named by the gitfile minus `/worktrees/<name>`: read it, never guess.

```bash
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
# A6  launch detached (a lane outlasts the foreground cap), from the scratch worktree
cd "$WT"
setsid nohup run-gate recovery_codes --worktree "$PWD" --json > /tmp/ciu215-naf-lane.json 2> /tmp/ciu215-naf-lane.err < /dev/null &
# wait in separate calls: tail -n 3 /tmp/ciu215-naf-lane.err ; kill -0 <pid of run-gate>
```

```bash
# A7  read the verdict (separate step)
python3 -c 'import json; d=json.load(open("/tmp/ciu215-naf-lane.json")); print({k: d.get(k) for k in ("verdict","exit_code","reason","log_path")}); print(d)' | head -40
LOG=<log_path>; grep -n -i "verdict\|GIT_FAILED\|Read-only file system\|fatal:\|could not create directory\|CIU-215" "$LOG"
```

Reading: apply the table in section 3. For this lane a test/coverage FAIL is probe PASS; only
`GIT_FAILED` / EROFS text fails the probe. Quote the final `run-gate: lane ... verdict ...` line.

### A8 rw sub-check (design note test 10: "check the `rw` layout once with in-lane commits")

Do this once, after A7, in the same scratch worktree. Hand-edit `git_common_dir = "ro"` to `"rw"` in
`tooling/test-runner/ciu.defaults.toml.j2` (Edit tool, one word), then recreate only the runners:

```bash
ciu down --dir tooling/test-runner && ciu up --dir tooling/test-runner 2>&1 | grep -n "git common dir mounted"   # PASS: "(rw)"
# repeat checks a and b unchanged (PASS as above), then:
docker exec "$RUNNER" git -c user.name=probe -c user.email=probe@invalid commit --allow-empty -m probe; echo "rc=$?"   # PASS: a commit line and rc=0
docker exec "$RUNNER" git -c user.name=probe -c user.email=probe@invalid worktree add --detach /tmp/probe-wt HEAD; echo "rc=$?"   # PASS (informational): refuses, Read-only file system on <COMMON>/worktrees/ (worktrees/ is ro in the rw layout)
git -C "$WT" log --oneline -1                                       # shows the probe commit: it is on the scratch branch only
```

Under `rw` the lane is NOT re-run for Run A unless A7 failed with GIT_FAILED (then re-run A6-A7 under `rw`
and compare, section 3a). The recreate may rebuild nothing: the image tag is unchanged.

### A9 cleanup (always, even after a failure)

```bash
cd /workspaces/netcup-api-filter                       # main checkout; an implementer cannot remove its own cwd
docker inspect --type container "$RUNNER" --format '{{.Name}}' # record both exact names first: "$RUNNER" and its -py311 sibling (same prefix)
ciu worktree rm "$NAME" --yes                           # runs ciu clean under that instance, then removes the checkout and the scratch branch if unmerged tip rules allow
ciu worktree list                                       # PASS: no "$NAME"
git worktree list | grep -c "$NAME"                     # PASS: 0
docker inspect --type container <exact py314 name>      # PASS: "No such object" (repeat for the py311 name)
docker volume inspect run-gate-state-py314-<...> 2>&1   # only if `ciu clean` was not clean; exact volume name from the earlier `docker inspect` mounts output
git branch --list "$NAME*"                              # PASS: empty. If it lists the scratch branch (no commits beyond main except the rw probe commit): git branch -D <that exact name>
rm -f /tmp/ciu215-naf.patch                             # logs /tmp/ciu215-naf-*.{log,json,err} stay until the results are copied into the table
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
# B0  preconditions: Run A cleaned up; same shell and ciu as section 2
cd /workspaces/dstdns
NAME=ciu215-probe-dst

# B1  window; create the scratch worktree WITHOUT --up (UNVERIFIED flags: `ciu worktree create --help`)
ciu worktree create "$NAME" --base main
ciu worktree inspect "$NAME" --json | python3 -c 'import json,sys; print(json.load(sys.stdin))'
WT=/workspaces/dstdns/.worktrees/$NAME                  # GUIDE: .worktrees/<name>; confirm against inspect
cat "$WT/.git"                                          # PASS: gitdir: <main>/.git/worktrees/<name>

# B2  the edit, scratch only
cd "$WT"
git apply --check /tmp/ciu215-dst.patch && git apply /tmp/ciu215-dst.patch
git status --short                                       # PASS: exactly the two .j2 files modified
grep -c "linked_worktree" tools/test-runner/ciu.defaults.toml.j2                   # PASS: 1
grep -c "worktrees/' in ciu.repo_root" tools/test-runner/ciu.compose.yml.j2        # PASS: 0

# B3  start the declared environment (core, db, runner) from the EDITED templates; detached, it is long
setsid nohup ciu worktree up "$NAME" > /tmp/ciu215-dst-up.log 2>&1 < /dev/null &
#     UNVERIFIED: `ciu worktree up` renders from the checkout's working files (uncommitted edits). The proof is B4's INFO line.
#     wait in separate calls; done when the process has exited; then:
tail -n 20 /tmp/ciu215-dst-up.log

# B4  proof the new ciu rendered the fragment for the runner stack, and the runner exists
grep -n "CIU-215" /tmp/ciu215-dst-up.log                # PASS: "[INFO] [CIU-215] git common dir mounted (ro) via .ciu/ciu.compose.gitdir.yml"
ls -l tools/test-runner/.ciu/ciu.compose.gitdir.yml     # PASS: exists
run-gate p128_persister_lineage --worktree "$PWD" --base HEAD~1 --dry-run 2>&1 | grep -n "container"    # UNVERIFIED flag order; `run-gate --help`
RUNNER=<container name from that line>                  # expect: dstdns-<INSTANCE_ID>-test-runner
docker inspect --type container "$RUNNER" --format '{{.State.Status}}'      # PASS: running
```

If `[CIU-215]` is missing from `/tmp/ciu215-dst-up.log` because `worktree up` re-ran an already-rendered
runner without recreating it: `ciu up --dir tools/test-runner` from `$WT` with the same exported
`PYTHONPATH`, and re-read B4. A runner that kept the old (no-mount) config shows no common-dir bind in `docker
inspect`: it must be recreated before the checks.

### Checks (a)-(c)

Identical to Run A (same table, same expected output and same `c2`/`mount` rows), with `$RUNNER` the dstdns
runner, the branch name from `ciu worktree inspect`, and the dstdns note that its image may need the
`safe.directory` env (`GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'`): use it only
as the documented retry in row (a), and record that it was needed.

### (d) The lane

```bash
# B6  detach; --base is required by the lane (changed_lines, base_source = request). HEAD~1 is any commit that
#     exists in the scratch checkout; the changed-lines coverage result is irrelevant to the probe.
cd "$WT"
setsid nohup run-gate p128_persister_lineage --worktree "$PWD" --base HEAD~1 --json > /tmp/ciu215-dst-lane.json 2> /tmp/ciu215-dst-lane.err < /dev/null &
# B7  read separately, as in A7 (section 3 rules):
python3 -c 'import json; d=json.load(open("/tmp/ciu215-dst-lane.json")); print({k: d.get(k) for k in ("verdict","exit_code","reason","log_path")}); print(d)' | head -40
LOG=<log_path>
grep -n -i "verdict\|GIT_FAILED\|Read-only file system\|fatal:\|could not create directory\|Permission denied\|CIU-215" "$LOG"
```

**What each outcome means for the open question.**

| B7 result | reading |
|---|---|
| verdict reached (any of PASS/FAIL by test or coverage), no `could not create directory` / `Read-only file system` text | **`ro` confirmed for dstdns**: the 2026-09-03 measurement is not reproducible on the current pinned judge (`tools/assay/assay-12.0.0.pyz`). Evidence to quote: the final verdict line, the INFO line from B4, rows a-c, the `mount` row |
| `ERROR/GIT_FAILED` and the log contains `fatal: could not create directory of '.../.git/worktrees/...': Read-only file system` | **`ro` refuted**: assay still runs `git worktree add` in the source. Go to section 7 |
| `ERROR/GIT_FAILED` with a message about removing/cleaning the snapshot, `Permission denied`, root-owned files | not the mount (dstdns FU-5): run the `rw` control B8; if `rw` fails identically, record "GIT_FAILED independent of mount mode" and the probe for dstdns is INCONCLUSIVE, not a pass or a refutation of `ro` |
| `ERROR/GIT_FAILED` with no message | run B8; compare the two logs line by line |

### B8 `rw` control and rw sub-check

Mandatory when B7 is GIT_FAILED; otherwise do the in-lane-commit sub-check only (design note test 10), as in A8:
change `"ro"` to `"rw"` in `tools/test-runner/ciu.defaults.toml.j2`, recreate the runner
(`ciu up --dir tools/test-runner`; the stack's other services stay up), confirm the INFO line now says `(rw)`,
repeat checks a and b (PASS as above), run the in-runner commit (PASS: `rc=0`, a commit line; the commit lands on the scratch branch only),
and, only when B7 failed, repeat B6-B7 under `rw` and record both verdict lines side by side.

### B9 cleanup (always)

```bash
cd /workspaces/dstdns
docker inspect --type container "$RUNNER" --format '{{.Name}}'      # record exact names of the runner and the ui runner (same prefix)
ciu worktree rm "$NAME" --yes                            # ciu clean under that instance (also removes leftover disposable p128 containers, which carry the instance prefix), then the checkout
ciu worktree list; git worktree list | grep -c "$NAME"  # PASS: not listed / 0
docker inspect --type container <each exact name recorded above>   # PASS: "No such object" each
git branch --list "$NAME*"                               # PASS: empty; else git branch -D <that exact name> (scratch branch only)
rm -f /tmp/ciu215-dst.patch
```

Step Z (only if you created the detached vbpub source checkout in section 2):
`git -C /workspaces/vbpub worktree remove --force "$VBPUB/.worktrees/ciu215-probe-src"`.

Verify the consumers' main checkouts were not touched: `git -C /workspaces/netcup-api-filter status --short` and
`git -C /workspaces/dstdns status --short` must show the same lines as before A0/B0 (P0 snapshot), and `git
log origin/main..` shows nothing pushed.

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
| lane (run-gate name / assay lane) | `recovery_codes` | `p128_persister_lineage` (`--base HEAD~1`) |
| lane `verdict` / `exit_code` / `reason` / assay outcome | | |
| final `run-gate: lane ... verdict` line | | |
| `GIT_FAILED` / `Read-only file system` / `could not create directory` in log (yes/no, line) | | |
| `rw` control run (needed? verdict line) | | |
| rw sub-check: a, b, commit rc=0, worktree-add refusal | | |
| probe reading (PASS / FAIL / INCONCLUSIVE) | | |
| cleanup verified (worktree list, `No such object` per name, branch gone) | | |
| wall time per step | | |

**Decision rule.** Both runs probe-PASS: CIU-215 can close (the close-when) and the design note's
recommendation stands; amend SPEC-V8 S16.4.9 / S6.2.4 to `ro` plus an opt-in `rw` with the RG-NARROW/RG-86
layout. Any run FAIL: section 7. Any run INCONCLUSIVE: repeat that run's lane step (not the whole run) after
fixing the stated cause; do not close CIU-215 or move the v8 spec on an INCONCLUSIVE.

---

## 7. If it fails

**Capture first, before cleanup** (all of it goes next to the results table; names exact):

1. The rendered fragment: `cat <stack dir>/.ciu/ciu.compose.gitdir.yml` (tooling/test-runner or tools/test-runner
   of the scratch worktree), and `ls -lR <stack dir>/.ciu/gitdir/`.
2. The runner's real mounts: `docker inspect --type container "$RUNNER" --format '{{json .Mounts}}'` and
   `... --format '{{json .HostConfig.Binds}}'`.
3. The lane log (`log_path`) and the `--json` result file; the lines matching
   `GIT_FAILED|Read-only file system|fatal:|could not create directory|Permission denied`.
4. In the runner, the exact failing git call reproduced by hand, with trace:
   `docker exec -e GIT_TRACE=1 "$RUNNER" git -C <logical checkout path> worktree add --detach /tmp/probe-wt HEAD`
   (shows whether the failure is `<COMMON>/worktrees/` creation, i.e. assay-style `git worktree add`).
5. Host side: `git -C <scratch worktree> rev-parse --git-dir --git-common-dir` and `cat <scratch worktree>/.git`.
6. ciu's own refusal text if any (`[CIU-215] ...`), verbatim.

**Recommendation when the dstdns lane fails under `ro` (or a netcup lane does) for a mount reason.** Switch
the design note's recommendation to `rw`, and the docs (CONSUMERS 5f, CONFIG.md, SPEC S16.9a) accordingly. The
evidence that supports it, in order of strength: (1) the `ro` log line `could not create directory of
'.../.git/worktrees/<snap>': Read-only file system` from a lane whose runner mount shows the common dir
`rw=false` (the mount row) and whose same lane PASSES or reaches a verdict under `rw` (B8 control); (2) the
trace in capture item 4 showing the `git worktree add` came from assay inside the runner (that would also
make the 2026-09-03 measurement valid against the current pinned judge and the note's "cannot be reproduced
from current code" statement wrong: say so); (3) rows a, b, c passing, i.e. the mount itself worked as
specified and only the lane's own write failed. Under `rw` keep the layout from the note (`worktrees/` ro
with the own admin dir rw on top, empty ro hooks dir, `info/` ro, sanitized config overlays), SPEC-V8
S16.4.9 / S6.2.4 stay on their flat-`rw` wording, and file the assay behaviour as an assay backlog entry
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
| Run B: lane `p128_persister_lineage` (budget 10m; plus a disposable PostgreSQL) | 10-15 min; with the `rw` control +15 min |
| Run B: cleanup | 5-10 min |
| **Total** | **about 2 to 3.5 hours** of host window, strictly sequential; ask the controller for 4 hours |

---

## 9. UNVERIFIED items (check each first, in this order)

1. **Venue**: that `ciu`, `run-gate`, `docker` and BOTH consumer checkouts exist in one shell on v1001 (P0),
   and which shell that is (host account or the cockpit container `mdt-cockpit-runner`, whose name comes from
   the private host table).
2. **vbpub source on the venue** contains `c64c2ffd8`, and the interpreter has Jinja2/PyYAML/tomli_w/
   `cli_extended` (section 2 check 2); that `PYTHONPATH` survives `ciu worktree`'s sanitized child
   environment (proof: the `[CIU-215]` INFO line).
3. `ciu worktree create <name> --base main` flags and default checkout dir (`.worktrees/<name>`) for
   netcup-api-filter; `ciu worktree inspect <name> --json` field names.
4. netcup `.env.services` in a fresh worktree (A3) and the estate pwmcp instance at `/srv/pwmcp-instance`
   on v1001; `ciu render --dir tooling/test-runner` writing the fragment (A3b).
5. netcup bringing up both py311 and py314 (no way found to start py314 only); first-build duration.
6. dstdns `ciu worktree up <name>` rendering from uncommitted edits, and its needing nothing beyond the
   declared `core` + `db` + `runner` on this host (vault init needs no operator input).
7. `run-gate` flag order for `--base`, `--json`, `--dry-run` (`run-gate --help`), the `--json` field names, and
   `HEAD~1` as an acceptable base for `p128_persister_lineage`.
8. The exact EROFS wording and `rc=128` of check (c) (git's text for a read-only index lock); the `safe.directory`
   behaviour of the netcup runner image (check a/b may need the documented retry).
9. Whether dstdns FU-5 (root-owned snapshot files) is still present with the current run-gate and assay
   (`dstdns` P171 report, measured at `84ac0b0b`); it can turn B7 into INCONCLUSIVE regardless of the mount.
10. All time estimates in section 8.
