<!-- origin: 2026-11-05; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/rg-102-PLAN.md -->
<!-- relation: RG-102/CIU-141 plan -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# RG-102 + CIU-141 implementation plan: `ciu run` one-shot lane containers

Planning agent (Sonnet), 2026-11-05. Read-only study; no repo edited, no host touched.
Sources read: run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md (RG-99 row :119, RG-102 row :122, entry :6337, amendment :6303-6311, pilot amendment :7536, RG-242 :7683), ciu/KNOWN_ISSUES_TODO_BACKLOG.md (CIU-141 :5397, row :592, CIU-176 :6140, CIU-193 :6171), ciu/src/ciu, ciu/docs/CLI-SURFACE-DECISIONS-20261104.md, run-gate-project/run_gate.py, dstdns `run-gate.toml`, `assay.toml`, `tools/test-runner/*`, `scripts/{schema-gate,assay_schema_runtime,p165-assay-schema,sql-mutation-gate}.sh`, reports `dst185-lanes-REPORT.md`, `r2-board-20261105b-REPORT.md` section 11.

## 0. Headline

- `ciu run` does not exist (grep over `ciu/src`, `cli_registry.py`, SPEC S10.8, `cli-surface.json`: no verb; `docs/CLI-SURFACE-DECISIONS-20261104.md:74` row C13 and `:94` CQ5 explicitly say "not added, deferred to v8"). That decision predates the 2026-10-15 operator direction and the operator's 2026-11-05 scheduling of RG-102; it must be amended in the same package (WP-C5).
- Everything CIU needs already exists as parts: identity/service resolution (`deploy.resolve_identities`, deploy.py:441; `exec_service`, deploy.py:812), the render pipeline (engine.py steps 1-15 in `main_execution`, engine.py:1854-2350), the ownership overlay (engine.py:1787), and the compose process boundary (engine.py:1414). What is missing is (a) a render-without-start entry point, (b) the one-shot compose launch with a CIU-owned container identity, (c) the launch event / detached-launch contract, (d) abandoned-one-shot reaping, (e) a typed per-run extension for what Run-Gate adds (limits, observer socket, group-add, forwarded env names, labels).
- Recommended shape: ONE new verb `ciu run [--detach]`. Foreground = human/dstdns/CLI use with exit-status passthrough and an optional JSONL events channel. `--detach --json` = the Run-Gate path: CIU launches the one-shot from the rendered service, prints exactly one JSON launch record (exact container ID) and exits; Run-Gate then continues with its existing, already-tested `container_state` -> `start_lane_profiling` -> `await_container` -> `docker rm` machinery (run_gate.py:16135-16339, 15032) unchanged. This swaps one argv builder (`docker run -d ...` at run_gate.py:16014) for a `ciu run --detach` call and leaves the RG-35 re-attach / inflight-record semantics intact, which is the cheapest correct way to get "exact ID before the lane finishes" and "status cannot be erased by `--rm`".
- Run-Gate declaration: `mode = "ephemeral"` plus a new `[environments.NAME.ciu]` table (`stack`, `service`, optional `profiles`, `root`). Not a fourth mode, because 40+ code paths branch on `mode == "ephemeral"` (observer, resources, inflight, probes) and all must keep applying.
- dstdns: the 10 group-A lanes move to a new `test-runner-svc` environment (ephemeral + `[ciu]`). Their provisioning scripts already create the disposable PostgreSQL by docker-outside-of-docker on the instance network and already reuse `SCHEMA_GATE_DSN` when exported; what breaks in a one-shot container is the scripts' notion of "my own container" (`hostname` and the persistent runner name), listed in section 4. The disposable PostgreSQL does NOT move into `ciu run`.
- Critical path is NOT the 80 mock lanes (pilot amendment :7536): this is exactly the 10 service lanes + `sql-mutation`.
- Sequence: WP-C0 spike (real docker) -> CIU WPs (7.24.0) -> Run-Gate WPs (25.2.0) -> dstdns lane change. The image rebuild is only needed if the dstdns test-runner image must carry a newer `ciu` client; it does not (the one-shot is launched by the controller, not inside the image), so no image rebuild is on the critical path (confirm in WP-D0).

## 1. Current state (file:line)

### ciu (main 59de0b232, version 7.23.2, CHANGES.md:24)

| Fact | Where |
| --- | --- |
| No `run` verb. `exec` (already-running service only, `docker exec`, "CIU does not start the stack") and `resolve` exist | `cli_registry.py:788-818`, handler `cli.py:1109-1139` (`_exec_service_cli`), `deploy.py:812-886` (`exec_service`) |
| `ciu run` deferred to v8, `gate` group name reserved | `docs/CLI-SURFACE-DECISIONS-20261104.md:74` (C13), `:75` (C14), `:94` (CQ5) |
| Selector `<stack>[:<service>]` parsing, single-service default, ambiguity refusal | `deploy.py:821-845` (reuse by extraction) |
| Identity render without writing files: network, compose project, container_name, image, working_dir; no cgroup parent, user, mounts, secrets | `deploy.py:441-640` (`resolve_identities`) |
| Compose project name rule (explicit `-p`, never cwd) and `--project-directory` | `engine.py:1269` (`compose_project_name`), `:1414-1450` (`execute_docker_compose_with_logs`) |
| Full pipeline: render global+stack (steps 2-5), hostdirs (8), pre_secrets hooks (9), secrets materialize (10), pre_compose hooks (11), configfiles (12), compose render (13), leak scan (14), overlay (15), `compose up -d` (16), post_compose hooks (17) | `engine.py:1899-2430` |
| Secrets travel in the compose process env (`expose_env`) and in the generated overlay (file secrets); both are part of `file_args` + `compose_env` | `composefile.py:1578` (`compose_process_env`), `engine.py:2312-2360` |
| Ownership labels `ciu.instance` / `ciu.repo-root` are stamped ONLY for a managed worktree instance, via a generated extra `-f` fragment | `engine.py:1549-1585`, `:1787-1823` |
| Network ownership re-verified immediately before Compose resolves the network | `engine.py:1431-1440` (`verify_workspace_runtime_network`) |
| `clean`/reset removes with `docker compose down -v --remove-orphans` (will sweep one-offs of the same project) | `engine.py:1100` |
| `ciu down` / `--stop` is not ownership-filtered (CIU-193, OPEN): a one-shot carrying the shared compose project label can be stopped by another checkout's `down` | ciu backlog `:6171` |
| Unprimed-shell identity seeding (`--root-folder`) already solved for `exec` | `cli.py:1057` (`_seed_selected_root_env`), `:138` (`_resolve_repo_root_deploy`) |
| CIU-176 (worktree stack renders main's template) FIXED on branch `ciu-176-20261105`, unreleased | ciu backlog `:6140` |
| Existing fake-docker test pattern for resolve/exec | `ciu/tests/tests/test_ciu_resolve_exec.py` |

### run-gate (25.1.2 on the controller host)

| Fact | Where |
| --- | --- |
| RG-102 OPEN, "blocked, CIU-141 not on main"; required contract and 7 oracles | backlog `:122`, `:6337-6350`; pilot amendment `:7536` |
| `mode` in {ephemeral, exec, host}; environment keys `image, image_env, cgroup_slice(_env), mode, container_name, forward_env, resources, state_root, user` | `run_gate.py:1054-1130` (`_validate_environment`), `:2468` (`resolve_environment`) |
| Ephemeral lane launch = raw `docker run -d --name <RG-242 name> --init --cgroup-parent <slice> <mounts> -e ... --memory/--pids-limit/--cpus <image> bash -c <inner>`; `started = subprocess.run(argv)` | `run_gate.py:15940` (`run_container_lane`), argv `:16014-16084`, run `:16114` |
| Container identity (RG-242): `<repo>-run-gate-<project>-<lane>-<sha8>-<yymmdd-HHMM>-<ns9>` + `run-gate.*` labels incl. `job`, `campaign` | `run_gate.py:15868-15935` (`lane_container_identity`) |
| After `docker run -d`: `inflight` record (container name, id, owner pid/start/boot/pidns), `container_state` inspect, `start_lane_profiling` (`ctl start` with container id), then `await_container` (log follow, `docker wait`, state collect) | `:16160-16339`, `:6876`, `:3770`, `:15032`, re-attach `:15526` |
| So today the observer is started AFTER the container has started; there is no first-instruction barrier in the raw path either | `:16221-16255` |
| Observer docker flags: read-only bind of the cgprofile observer dir, `--group-add <observer gid>`, env var naming the socket; host socket/dir ownership and mode checks | `:8733-8810` |
| Ephemeral mount set = judged worktree (dual phys+namespace), git common dir, sanitized git config overlay, hooks/info, worktree pointer, optional state root, cowtree | `:14209-14270` |
| `slice` resolved from environment (`cgroup_slice(_env)`) and verified loaded before launch | `:4578`, `:4604` |
| Probe launches (doctor, `assay lanes --json`, state-root assure) are also raw `docker run --rm` with the same mounts/user | `:8860-8905` (`build_env_probe_argv`) |
| Exec runner path (`docker exec` into a persistent CIU runner; RG-99 lock-free git config) | `:16363` (`run_exec_lane`) |
| Observer requires `mode = "ephemeral"` | `:8754-8756` |
| cgprofile control socket only; no `docker exec` carrier (RG-156) | backlog `:7556` |

### dstdns (branch `dst185-lanes-20261105` @ 8bf2ce3c, unmerged, review pending)

- `run-gate.toml:206-213` `[environments.test-runner-r2]` = ephemeral, `user = "invoker"`, `cgroup_slice_env = "CGROUP_PARENT_DEV_GATES"`; comment at `:195-204` names RG-102 as the unblock for service lanes.
- Exec `test-runner` (`run-gate.toml:160-172`) = persistent per-checkout CIU stack `tools/test-runner`; its compose declares user `CONTAINER_UID:GID`, `container_name` and `hostname` fixed (`<project>-<tag>-test-runner`), `/var/run/docker.sock` mounted, the checkout bind at `ciu.repo_root`, the `run-gate-state` named volume at `state_root`, linked-worktree git-common bind, `tty: true`, `stdin_open: true`, the instance network, `REPO_ROOT`, live-stack env incl. secrets, `cap_add IPC_LOCK` (`tools/test-runner/ciu.compose.yml.j2`). A `pre_compose` hook publishes the identity cache `.ciu/resolved-identities.json` (`tools/test-runner/pre_compose_resolved_identities.py`).
- The 10 group-A lanes (`p165_report_access_r2`, `p167_created_by_r2`, `p201_workflow_{service x3,outbox x2,cancel_sched x2}_r2_*`, sql-mutation `cw2b_schema`) are refused today for exec-mode/no-measurement reasons (`dst185-lanes-REPORT.md`, group A; `r2-board-20261105b-REPORT.md` section 11). They run scripts that provision a disposable PG (`scripts/p165-assay-schema.sh` etc., reuse `SCHEMA_GATE_DSN` if exported) or need `SCHEMA_GATE_DSN` + `SCHEMA_GATE_PG_DUMP` exported (`assay.toml:134-137`, `scripts/sql-mutation-gate.sh:61,102-123`, a leased DSN pool of `infra/schema-gate-pg` instances).
- Scripts that assume a persistent runner: `scripts/assay_schema_runtime.sh:12-15,136-155` (own container = `hostname`; instance prefix cross-checked as a prefix of the hostname; image, mounts, network, cgroup read from `docker inspect <hostname>`), `scripts/schema-gate.sh:224` (`docker exec` into the persistent runner named by the identity cache).

## 2. The ciu primitive: exact contract

### 2.1 Verb syntax

```
ciu run [--root-folder P] [--profile NAME ...]
        [--name CONTAINER_NAME] [--label K=V ...]
        [--env-name VAR ...]           # forward VAR from the calling environment (value never on argv)
        [--extend FILE]                # typed per-run compose fragment, see 2.4
        [--detach] [--json]
        [--events-fd N | --events-file PATH]
        [--dry-run]                    # resolve + validate + print the plan; start nothing
        [--skip-hooks]
        <stack>[:<service>] -- ARGV...
ciu run reap [--root-folder P] [--older-than DUR] [--yes]
```

- `<stack>[:<service>]`: same grammar and single-service default as `ciu exec` (extract `_select_service` from `deploy.exec_service`). Missing/ambiguous service, no rendered identity, unreadable root, render failure, unavailable Docker: refuse before any container exists, exit 2 (usage/config) or 1 (runtime), named remedy, per the existing exit table.
- ARGV after `--` is passed as exact argv, no shell (`compose run SERVICE ARGV...` replaces the service command; the image/service entrypoint is kept, like `exec`'s argv model). `--entrypoint` is not exposed in v1.
- Not on `ciu run`: `--rm` (always one-shot; foreground removes after the status is collected, detached never auto-removes), `--user` (service authoritative), `--no-deps` (always on; CIU stacks order via `ciu up`, never via compose `depends_on`; refuse a `--with-deps` request), `-p` publish, `--service-ports`.
- Output discipline: in foreground, only the child's stdout/stderr go to ciu's stdout/stderr; every CIU diagnostic (the step banners printed by the pipeline) goes to stderr (redirect stdout to stderr during prepare). Required so `ciu run ... -- some-probe` output can be captured by Run-Gate's probes. Secret values never printed (the pipeline's redaction contract stays).

### 2.2 What "from the rendered service" means (authoritative vs allowed additions)

Authoritative from the service declaration, never overridable by flags: image, user, network(s), CIU secrets (env and file), service-declared mounts and named volumes (including dstdns's `run-gate-state` volume and the checkout bind), cgroup parent, working dir, tty/stdin_open, cap_add, hostname, service labels, restart policy (ignored for run).

Implementation: factor `engine.main_execution` steps 1-15 into `engine.prepare_stack(...) -> PreparedStack` (`working_dir`, `file_args`, `compose_env`, `project`, `rendered_compose`, `ownership_path`) with `main_execution` = prepare + step 16/17 (pure refactor, WP-C1). `ciu run` = prepare (skipping step 6 reset) then `compose run`. Hooks: `pre_secrets` and `pre_compose` hooks DO run (dstdns's identity-cache hook is a pre_compose hook and its output is read by lane scripts), `post_compose` hooks do not (nothing came up); `--skip-hooks` available. Hostdir creation (step 8) and secret materialization (step 10) run as in `up` (idempotent). A per-stack advisory lock (flock on `<stack>/.machine/.ciu-run.lock`) serialises the PREPARE phase only, so concurrent `ciu run` calls never race the atomic-replaced `ciu.compose.yml`, the overlay or first-time GEN_LOCAL secret generation; the container launch itself runs outside the lock.

Compose command (project identity identical to `ciu up`, same named volumes and network):

```
docker compose -p <project> --project-directory <repo_root> <file_args from prepare>
    [-f <per-run extension file, 2.4>] run -d --no-deps --name <NAME> -l <k>=<v> ... -e VAR ... SERVICE ARGV...
```

Same project name is mandatory: the `run-gate-state` named volume and any service volume are project-scoped; a separate project would silently give the lane a different state volume.

### 2.3 Container identity, names, labels, ownership

- Name: caller-supplied `--name` is used verbatim after validation (`[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}`, must not exist; Docker refuses duplicates so the loser fails loudly). Run-Gate passes its RG-242 name (`<repo>-run-gate-<project>-<lane>-<sha8>-<stamp>-<ns9>`), so the "run-gate vs ciu naming" question resolves as: the RG-242 name is the container name, ciu's own relation is in labels. Without `--name` CIU generates `<compose_project>-<service>-run-<12hex>` (compose's own one-off form, deterministic length, never the service's fixed `container_name`, which compose `run` must not reuse; verify in WP-C0).
- Labels stamped by CIU (reserved `ciu.` namespace; `--label` refuses keys starting `ciu.`, `com.docker.`):
  - `ciu.run=1`, `ciu.run.id=<uuid4>`, `ciu.run.stack=<repo-relative stack>`, `ciu.run.service=<svc>`, `ciu.run.owner-pid`, `ciu.run.owner-start` (process start ticks), `ciu.run.boot-id`, `ciu.run.pidns` (the same recycled-pid defence Run-Gate's inflight record uses, `run_gate.py:16160-16200`), `ciu.run.mode=foreground|detached`, `ciu.run.started=<iso>`.
  - `ciu.instance` and `ciu.repo-root` for a managed worktree instance, through the existing ownership overlay (`engine.write_ownership_overlay`, applied to the one-shot too; write it to a per-run temp directory instead of the shared `.machine/` file so two concurrent runs cannot clobber one overlay). For a PRIMARY checkout (no `ciu.worktree-instance.json`) the existing rule stands: unlabeled by ownership, but `ciu.run.*` labels still carry the owner pid and the exact `com.docker.compose.project`.
  - Compose adds `com.docker.compose.project`, `.service`, `.oneoff=True`.
- Sweeps: `ciu clean` removes one-offs of its project via `down -v --remove-orphans` (engine.py:1100) (verify in WP-C0 that one-offs are included). `ciu down`/`--stop` ownership filtering stays CIU-193's job; the new labels give it the key. Plan dependency: CIU-193's fix is NOT a prerequisite, but WP-C4's acceptance must show a one-shot is not stopped by a sibling checkout's `ciu down` only if CIU-193 is merged; otherwise record the exposure in the verb's docs (low risk: lane containers last minutes and run-gate detects `await_container` state change).

### 2.4 The typed per-run extension (`--extend FILE`)

A YAML fragment, validated by CIU and merged as the LAST `-f`. It carries what Run-Gate adds for a lane and is the "narrowly typed one-shot attachment". Allowed keys under `services.<svc>` only:

| Key | Rule |
| --- | --- |
| `mem_limit`, `memswap_limit`, `pids_limit`, `cpus` | allowed; REFUSED if the service already declares the same key (v1: no merge arithmetic; remedy names the key and both values) |
| `group_add` | list of numeric gids (observer gid) |
| `volumes` | long-form `type: bind` (source absolute, existing, no `..`) or `type: volume` named; `read_only` honoured; REFUSED if the target collides with a service-declared target |
| `environment` | map of non-secret literals only; values that match any CIU secret value are refused (the pipeline's leak scan runs over the fragment) |
| `labels` | merged under the reserved-prefix rule above |

Anything else (`cgroup_parent`, `user`, `network*`, `image`, `privileged`, `devices`, `secrets`, `command`, `entrypoint`, ports, `pid`, `ipc`, `cap_add`, `security_opt`, `extra_hosts`, `cgroup`) is a refusal naming the key. The observer socket bind must be `readonly` and its source must equal a path the caller names in the `ciu.run.observer-socket-dir` extension annotation: v1 validates only "bind, read_only, directory"; the host socket ownership checks stay in Run-Gate (`resource_observer_docker_flags`, run_gate.py:8733-8810), which owns that security reasoning and builds the fragment.

Values for `--env-name VAR`: compose `run -e VAR` with the value in ciu's process environment (never argv; same discipline as RG-117a). CIU additionally injects, non-secret: `CIU_RUN_CONTAINER_NAME=<name>`, `CIU_RUN_ID=<uuid>` (this is what dstdns scripts use instead of `hostname`, section 4), and `CIU_RUN_CGROUP_PARENT` only if the service declares it (read from the resolved config, not echoed from the caller).

### 2.5 Plan document (`--dry-run --json`)

Prints, without starting anything and without environment values: `{schema:"ciu-run-plan/1", project, stack, service, image, user, working_dir, cgroup_parent, networks[], mounts[{type,source,target,ro}], limits{...}, labels{...}, name, extend_accepted, hooks:[...]}`. Source: `docker compose ... config --format json` parsed in memory, then projected to this allow-list (the raw `config` output interpolates secrets and is never printed or logged). Run-Gate uses it for `--dry-run`, for `verify_slice_loaded` (it needs the service's effective cgroup parent BEFORE launch, run_gate.py:4604) and to prove the service mounts the declared `state_root`.

### 2.6 Launch event and exit contract

Event schema `ciu-run-event/1`, JSON, one object per line:

```
{"schema":"ciu-run-event/1","event":"launched","run_id":"...","container":{"id":"<64 hex>","name":"..."},
 "project":"...","stack":"tools/test-runner","service":"test-runner","image":"...","image_id":"sha256:...",
 "user":"1003:1003","cgroup_parent":"...","cgroupns":"private","networks":["..."],"labels":{...},"at":"<iso>"}
{"schema":"ciu-run-event/1","event":"exited","run_id":"...","exit_code":N,"oom_killed":false,"finished_at":"<iso>"}
{"schema":"ciu-run-event/1","event":"removed","run_id":"...","removed":true,"error":null}
```

- `container.id` comes from `docker inspect <exact name>` (full ID), never from output scraping or substring search. `cgroupns` and `cgroup_parent` are reported from the inspected `HostConfig` of the created container, so Run-Gate can assert private cgroup namespace and record the real placement (replaces `slice_name` for ciu-backed environments).
- Channel: `--events-fd N` (N >= 3, must be an open writable fd inherited from the caller; CIU does not open it) or `--events-file PATH` (append, created 0600). Never stdout/stderr. In `--detach --json` mode the single `launched` object is also the stdout document (exit 0 after the container is started and inspected); no `exited`/`removed` events exist because CIU does not outlive the launch.
- Foreground lifecycle (implementation (B), pending WP-C0): `compose run -d --name N` -> inspect -> emit `launched` -> `docker attach` (sig-proxy, TTY/stdin as the service declares) -> `docker wait`-equivalent exit read from `State.ExitCode` -> emit `exited` -> `docker rm -f N` by exact name -> emit `removed`. Cleanup therefore cannot race ahead of status collection. Exit code of `ciu run` = the container's exit code verbatim (128+n signal codes pass through); CIU-owned failures use 1/2 per the existing table and are distinguishable because the `exited` event is absent. Fallback (A) if the spike shows (B) breaks TTY/stdin semantics: foreground `compose run --rm` with an exact-name inspect watcher; then the observer-aware path is `--detach` only.
- Interruption (SIGINT/SIGTERM to ciu in foreground): forward to the container once, wait the grace period, `docker rm -f` by exact name after status collection, emit `exited` (if known) + `removed`, exit 128+signal.
- Detached lifecycle owner is the caller (Run-Gate). CIU leaves the container, labeled `ciu.run.mode=detached` with the owner identity of the CALLER passed via `--owner-pid PID` (default: parent pid), so `reap` is accurate. Removal by Run-Gate stays `docker rm -f <exact name>` after `await_container` collects status and the observer session is stopped (today's behavior).
- `ciu run reap [--root-folder P] [--older-than DUR]`: lists `ciu.run=1` containers of THIS root's compose project (and, for a managed worktree, matching `ciu.instance`/`ciu.repo-root`), classifies each by owner liveness (pid exists AND start ticks AND boot id AND pid namespace match), and removes by exact id only those whose owner is dead and that are exited or older than the bound; it prints what it left alone and why. Never filters by image or ancestor (standing rule: remove by exact name only).

### 2.7 CIU-176 / worktree interplay

- `ciu run --root-folder <worktree>` resolves that worktree's OWN rendered config and generated facts exactly like `ciu exec` (`_seed_selected_root_env`), so a managed worktree's one-shot attaches to the worktree instance network and mounts the worktree checkout (dstdns's compose renders the git-common-dir bind only for `/.worktrees/` roots; the service declaration, not Run-Gate, supplies it).
- It depends on CIU-176's fix (branch `ciu-176-20261105`, ref `ffd937926`, not merged): without it a worktree render can read the main checkout's template. WP-C2 must be cut from a main that contains the CIU-176 merge; if that merge is not in main by then, the work package rebases onto it and says so in its report.
- The managed instance must be `ready` (record state), and the instance network must exist (`verify_workspace_runtime_network`, reused unchanged). `ciu run` does NOT take the instance lease nor the CIU-24 worktree budget slot (a one-shot starts no new instance; `engine.py:2392-2400` budget slot is `up`-only). Documented.

### 2.8 Secrets / network / cgroup / mounts / user proof

All five come from the service via the prepared file args; oracle (2) and the live acceptance assert each on a fixture (secret visible only as the declared env/file, never in `ciu run` output; network = instance network; mounts = declared set (+ typed extension); cgroup parent = declared; user = declared non-root).

## 3. Run-Gate adapter contract

### 3.1 Declaration

```toml
[environments.test-runner-svc]
mode = "ephemeral"
state_root = "/run-gate-state"           # must be a target the service already mounts (checked via the plan)
forward_env = ["SCHEMA_GATE_DSN", "SCHEMA_GATE_PG_DUMP"]
[environments.test-runner-svc.ciu]
stack = "tools/test-runner"              # exact repo-relative stack, required
service = "test-runner"                  # exact compose key, required
profiles = []                            # optional, same meaning as ciu --profile
root = "."                               # CIU root relative to the judged worktree, default "."
```

Validation (`_validate_environment`, run_gate.py:1054): `[.ciu]` legal only with `mode = "ephemeral"`; with `[.ciu]` the keys `image`, `image_env`, `user`, `cgroup_slice`, `cgroup_slice_env`, `container_name` are REFUSED at load with a message naming CIU as the authority (no silent precedence, no duplicate service description); `resources.cpus` fallback stays; `forward_env`, `state_root`, `resources` stay. Unknown `[.ciu]` keys refused. No raw-docker fallback and no persistent-exec fallback anywhere (RG-102 required contract).

### 3.2 Launch path (replaces only the argv assembly at run_gate.py:16014-16114)

1. Resolve `ciu` on PATH (refuse with the install remedy if absent; require ciu >= 7.24.0 via `ciu --version`, refuse older with the version in the message; no sibling import, per the estate "separate projects" rule).
2. `ciu run --root-folder <root> [--profile ...] --dry-run --json <stack>:<service> -- true` (plan): gives `cgroup_parent` (-> `verify_slice_loaded`, `CGROUP_ENV_VAR`), user, image (-> disclosure and `image_selection` record), mounts (-> state_root check, refuse if the declared target is not mounted by the service), networks. Refusals here are ordinary lane ERRORs with CIU's remedy text, before any container exists.
3. Build the `--extend` fragment: limits from `resources` (`mem_limit`, `memswap_limit`, `pids_limit`, `cpus`), the observer read-only bind + `group_add` from `resource_observer_docker_flags` logic (kept in Run-Gate, converted to the typed fragment), cowtree bind if `resources.cowtree` applies. Run-Gate does NOT add the dual worktree/git mounts for a `[.ciu]` environment: the service declaration is authoritative (parity with exec mode; the lost hardening is the sanitized git config overlay, see risks).
4. `ciu run --detach --json --name <RG-242 name> --label run-gate.*=... --env-name <forward_env> --env-name PYTEST_ADDOPTS... --extend <fragment> <stack>:<service> -- bash -c <inner>`; the existing `lane_container_identity` stays the name/labels source (including `RUN_GATE_JOB`/`RUN_GATE_CAMPAIGN`), plus passes the new `ciu.run.id` back into the inflight record. Parse the single JSON launch record; `container.id` replaces `started.stdout`'s id in `inflight_payload["container_id"]`.
5. Everything after is unchanged: `write_inflight_record`, `container_state`, `start_lane_profiling` with the exact id from the record (not rediscovered), `await_container`, evidence, `docker rm -f <exact name>`. RG-35 re-attach after a client death works unmodified because the inflight record names the container exactly as today.
6. Probes (doctor, `assay lanes --json`, `assure_assay_state_root`; `build_env_probe_argv`, run_gate.py:8860-8905) for a `[.ciu]` environment use the same adapter in foreground mode with stdout capture (hence the output-discipline rule 2.1), under the same environment, so doctor/inventory/state-root evidence describe the real runner, not an image approximation.

### 3.3 Observer / admission / R2

- Admission (published concurrent-lane count, host headroom read, `prepare_profile_start`) is unchanged and still precedes the launch.
- cgprofile session: started by Run-Gate with `container.id` from the launch record; stopped by Run-Gate after `await_container`; no output scraping, no name-substring discovery. The observer-only socket arrives through the typed `--extend` bind (service stays free of it; dstdns services do not need to know about cgprofile), with the host-side validation in Run-Gate, and the `CGPROFILE...` env var through `--env-name` or the fragment's literal `environment`. The observed GID rides `group_add`. `RG55-INTERFACE-CONTRACT.md` needs no change if the cgroup path continues to come from the daemon's own `ctl start` response (it does today, run_gate.py:16221-16255); WP-R2 checks that and edits the contract and its byte-identical mirror only if needed.
- Ordering decision: v1 keeps today's ordering (observer attaches after start). The container is not auto-removed, so terminal status collection cannot race (oracle 6a). First-instruction sampling needs a readiness barrier; it is OPT-IN follow-up work (`--hold` start gate, section 5 WP-X1) and is only built if the operator wants it (open question Q3). The live acceptance records what the daemon does for an immediate-exit lane (profile start on an already-exited container) and the lane result must say "observer attached late/unavailable" honestly, not claim coverage from instruction 1.
- Campaign use: an R2 campaign runs N candidate lanes via Assay inside ONE lane container (one container per lane invocation), exactly as the raw ephemeral R2 today; `--jobs` parallelism is Assay-internal. Two concurrent LANES = two `ciu run --detach` calls = two containers, two users, no shared `/tmp`, no shared PID 1 (RG-99/RG-84 by construction).

## 4. How the 10 dstdns service lanes then run

What stays: the provisioning scripts' docker-outside-of-docker PostgreSQL (the one-shot has the docker socket and the instance network from the service), `SCHEMA_GATE_DSN` reuse, schema apply, assay budgets. What changes in dstdns (separate package after RG-102 lands; one dstdns worktree, Codex implementer, independent review):

1. `run-gate.toml`: add `[environments.test-runner-svc]` (3.1); move the 10 group-A lanes from `[assay] environment = "test-runner"` inheritance to explicit `environment = "test-runner-svc"`, `resource_observer = "cgprofile"`, `clean_tree = false`, `resources` like their siblings (memory 2g / pids 8192 / cowtree auto); update `tests/config/test_assay_pin_integrity.py` (`EXPECTED_R2_SERVICE_LANES`, the service-lane marker) and RIGOR-COVERAGE-POLICY.md:83 / COVERAGE-MAP.md: the "refused until RG-102" guard flips to "must be on `test-runner-svc`".
2. `scripts/assay_schema_runtime.sh`: own-container identity = `${CIU_RUN_CONTAINER_NAME}` (injected by `ciu run`, 2.4) when set, else `hostname` (exec runner, still supported); the instance-prefix cross-check changes from "prefix of the runner hostname" to two independent read facts that hold for a one-shot: the network name suffix `-network` and the `com.docker.compose.project` label of the inspected own container. (`hostname` of a one-shot equals the service's FIXED hostname, `<project>-<tag>-test-runner`, which no longer names this container; compose `run` does not take the service's `container_name`.)
3. `scripts/schema-gate.sh:224` (`docker exec` into the persistent runner): run pytest in-process when `CIU_RUN_CONTAINER_NAME` is set (the one-shot IS the runner), keep the exec form otherwise. Only the `schema`, `scale-admission` command lanes use this; they are not among the 10 but share the script, so the test pins both branches.
4. `sql-mutation` (`cw2b_schema`): provisioned `infra/schema-gate-pg` pool via the existing `ciu up --dir infra/schema-gate-pg` + operator-exported `SCHEMA_GATE_DSN` (password-bearing DSN) and `SCHEMA_GATE_PG_DUMP`, delivered by `forward_env` (name only on argv, value in client env, existing Run-Gate mechanism). The `docker exec "$SERVER_HOST"` calls in `sql-mutation-gate.sh` work from the one-shot (docker socket, same instance network). A secret-clean alternative (the test-runner stack declaring the schema-gate-pg password as a CIU secret) is a later improvement (Q5), not v1.
5. `CGROUP_PARENT_DEV_BACKGROUND` (hard requirement of every provisioning script): comes from the controller's env via `forward_env`/`--env-name` as in exec mode today (`forward_env` list on `test-runner`), or from the service's own rendered env; WP-D0 reads which, and the lane preflight refuses with the name if absent.
6. First acceptance lane = `p165_report_access_r2` (smallest), then `p201_workflow_outbox_r2_compare`, then `cw2b_schema`, then the rest as one batch; each run starts with the baseline R0 only (failing baseline ends in seconds, per the RIGOR policy) before a campaign.
7. Known limit to document in the lane comments: the DooD PostgreSQL and the p165-style `-runner` clone containers are siblings, not inside the observed lane container; the observer measures the lane container. Same blind spot as exec today, now stated honestly in the R2 evidence.

## 5. Work packages (dependency order, sized for Codex implementers)

All implementers: Codex luna max; independent review sol xhigh; one Fable review per tool at the end (standing rules). Each package = one worktree/branch, tests first (oracle-first), registered gate green, no push/merge by the implementer. "Fakes" = a scripted fake `docker` shim on PATH (existing pattern in `test_ciu_resolve_exec.py` and run-gate's docker shim tests); "real docker" = real daemon with Compose v2 (a DinD sidecar via the cmru tester path is sufficient for everything except cgprofile).

### WP-C0 spike (real docker; 0.5 day; read-only evidence report, no repo change)
Measure on a host with the Compose version the estate runs: (1) `compose run -d` stdout (name vs id); (2) `--name` with a service that sets `container_name` and `hostname`; (3) `-l`, `-e VAR` (value inheritance), `--no-deps`; (4) does `run -d` honour the service `tty`/`stdin_open`; can `docker attach` in foreground then give correct TTY/stdin/exit semantics (decides implementation (B) vs (A)); (5) which `compose down --remove-orphans` removes one-offs; (6) client SIGKILL during foreground `run --rm` leaves the container? ; (7) immediate-exit command: is the container inspectable and its exit code collectable after `-d`; (8) `config --format json` field names for `cgroup_parent`, `user`, `volumes`, `mem_limit`, `cpus`; (9) per-run extension merge semantics (list append vs target-replace for `volumes`). Output: a short table appended to this plan's folder; resolves Q-spike items below. Gate for all later CIU WPs.

### WP-C1 prepare/up split (ciu; fakes; 1 day)
`engine.prepare_stack()` extracted from `main_execution` steps 1-15; `main_execution` calls it. No behaviour change. Files: `src/ciu/engine.py`, `tests/tests/test_ciu_engine_execution_boundaries.py`. Oracles: (a) the existing engine/deploy test suite unchanged; (b) golden test: for a fixture stack, `up --dry-run` renders byte-identical `ciu.compose.yml`, overlay and `file_args` before/after; (c) controlled wrong implementation (prepare runs step 6 reset) is caught.

### WP-C2 `ciu run --dry-run --json` + refusals + extension validator (ciu; fakes; 1.5 days; depends C1, C0)
New `src/ciu/oneshot.py` (selector extraction shared with `exec_service`, plan builder from `compose config --format json` allow-list projection, extension validator, name/label validation), `cli_registry.py` + `cli.py` handler, `cli-surface.json` + SPEC S10.8 regeneration. Oracles = CIU-141 (1) unprimed `--root-folder P`, (2) fixture service retains network/secret/mounts/cgroup/user in the PLAN, (4) refusals before launch (missing/ambiguous service, missing identity, render failure, no docker, bad extension keys each named), (5-part) secrets never in plan/stdout (planted secret value scan), extension collisions refused, reserved labels refused. Tests in `tests/tests/test_ciu_run_plan.py`.

### WP-C3 foreground + detached launch, events, locking (ciu; fakes + real docker; 2 days; depends C2)
`oneshot.run_foreground`, `run_detached`, events writer (fd/file), per-stack prepare lock, per-run temp overlay, exit/signal passthrough, interruption cleanup, stdout/stderr discipline. Oracles: (3) two concurrent invocations get distinct IDs, distinct `/tmp`, each reaches its own body (fake: distinct names/ids; real: two containers printing `$$`/`hostname`/`id -u`); (5) exit status passthrough for 0/1/42/130/137, SIGINT cleanup, no secret in output; launch event arrives before the command finishes (fake: command blocks on a file the test releases only after reading the event); events never on stdout/stderr (stream-split test); concurrent prepare race test (two processes, GEN_LOCAL secret generated once). Real-docker part runs in WP-C5.

### WP-C4 reap + abandoned controller (ciu; fakes + real docker; 1 day; depends C3)
`ciu run reap`, owner-liveness rule (pid+start+boot+pidns), exact-id removal only, "left alone" report. Oracles: (9) kill -9 the controller after launch, `reap` removes exactly that one-shot and leaves a concurrently running sibling and a different-root container; recycled-pid fake (same pid, different start ticks) is treated as dead; never uses an image/ancestor filter (assert on the fake docker's recorded argv). Controlled wrong implementation: reap by ancestor filter must fail the sibling test.

### WP-C5 docs, spec, backlog, live acceptance (ciu; real docker; 1.5 days; depends C4)
Docs: SPEC S10.8 + S8, README, CONSUMERS.md ("one container per lane" recipe), DESIGN-GUIDE, CLI-SURFACE-DECISIONS (C13/CQ5 amended: run is v7, `gate` stays reserved), CHANGES.md (`feat(ciu): ciu run`), backlog CIU-141 status. Live acceptance: registered lane (patterned on `[lanes.enroll]`, DinD, `ciu/run-gate.toml:67`) that builds a fixture stack (network, CIU GEN_LOCAL secret, ro + rw mounts, `cgroup_parent`, non-root user, tty+stdin_open service) and proves oracle (6): attaches to the declared network, sees only the rendered secret, declared mounts/cgroup/user, removed on success/failure/interruption; immediate-exit command; two concurrent runs; controller-kill + reap. Needs a real Docker daemon; no cgprofile.

### Release R-ciu: ciu 7.24.0 (minor; `feat`). Via cmru per the estate release workflow (R2 async on latest only); deploy the wheel to the devcontainer/controller hosts; this unblocks everything below. CIU-176 must be in main first (2.7).

### WP-R1 Run-Gate schema + launch adapter (run-gate; fakes; 2 days; depends R-ciu wheel or a fake `ciu` shim in tests)
`_validate_environment` `[.ciu]` table + refusals, `resolve_environment` carrying it, adapter functions (`ciu_run_plan`, `ciu_run_launch`, extension-fragment builder from the existing observer/limits/cowtree logic) replacing the argv assembly in `run_container_lane`, probes via the foreground adapter, doctor lines (`ciu` present, version floor, plan resolves). Files: `run_gate.py`, README, DESIGN-GUIDE, CONSUMERS, SPEC (R-07/R-19a/R-14 area + new R-ciu clause), LANE-AUTHORING, CHANGES, backlog. Tests: a fake `ciu` shim speaking the contract (plan and launch JSON); oracles 1 (lane starts with no running CIU service: shim records `ciu run` argv, no `docker run`, no `docker exec`), 2 (two concurrent lanes, distinct ids), 3 (missing/ambiguous service or unready instance refuses before body; assert NO `docker run`/`docker exec` argv at all = no fallback), 4 (argv/TTY/exit/budget/timeout/state-root unchanged vs raw ephemeral golden), 7 (success/failure/timeout/interrupt leave nothing: shim container table empty). Controlled wrong implementations: fall back to raw docker on refusal; rediscover container by name substring; both must fail tests.

### WP-R2 Observer integration (run-gate + fake daemon; 1 day; depends WP-R1)
Observer fragment ro bind + group_add + env through the typed extension; `start_lane_profiling` fed the launch record's id; `cgroupns`/`cgroup_parent` from the record into placement/inflight; immediate-exit lane test with the in-process fake socket daemon (`set_cgprofile_plan`): terminal status collected, no race, honest "attached late" outcome. Oracle 5 and 6 (fake level). Contract-doc check (RG55-INTERFACE-CONTRACT.md + mirror) only if the identity contract changes.

### WP-R3 Live acceptance + release (run-gate; real docker host + cgprofile; 1 day; depends WP-R2, R-ciu deployed)
One live CIU + Run-Gate + cgprofile acceptance on a controller host that has the daemon (r1002 or v1001; not <protected-host>): fixture or dstdns stack, `observer` verdict from a one-shot lane, ordering with an immediate-exit command, kill the run-gate client mid-lane and re-attach (RG-35), `ciu run reap`. The registered Run-Gate gate must pass. Release: run-gate 25.2.0 (additive minor), then deploy; the run-gate tester image rebuild is required only if the image bakes the run-gate wheel (it does for the estate tester image: rebuild + digest bump per the estate release flow) and NOT for dstdns's `test-runner` image.

### WP-D1 dstdns lane migration (dstdns; fakes for config tests + real host for the 10 lanes; 2 days; depends R-run-gate deployed; branch off dstdns main after `dst185-lanes-20261105` merges)
Section 4 items 1-5. Tests: the convention test flips (service lanes on `test-runner-svc`), shell-script unit tests for the two identity branches (`CIU_RUN_CONTAINER_NAME` set/unset) with a fake docker, the 10-lane list derived from `SCHEMA_GATE_DSN` marker (as the DST-185 guard already does). Real-host verification: `run-gate p165_report_access_r2` baseline then campaign on an R2 host (host-compute operator), then the others; DST-185 closes only with real verdicts.

### WP-X1 (optional, only on operator yes to Q3) readiness barrier: `ciu run --hold`
Create/start split via an entrypoint wrapper from a ciu-provided read-only mount blocking on a FIFO until the caller writes `release` on a control fd; resolves original entrypoint from `compose config` / image inspect; needs `/bin/sh` in the image (dstdns has bash). Oracle: immediate-exit command, observer attached before the first instruction (the lane writes a monotonic stamp, the observer session start is earlier). Skipped unless first-instruction sampling is required.

### Release order and what runs where
1. WP-C0 (host) -> WP-C1..C5 (devcontainer fakes; C3/C5 real docker via DinD tester) -> review -> merge -> **ciu 7.24.0 release** (cmru) -> deploy ciu wheel to controller hosts.
2. WP-R1, R2 (fakes) can start in parallel with C3-C5 against the shim; final integration waits for the ciu wheel. WP-R3 live -> **run-gate 25.2.0 release** -> tester image rebuild/digest bump -> deploy.
3. WP-D1 -> real-host R2 campaigns -> DST-185 close, RG-102/CIU-141/RG-99 rows updated (RG-99's "live exec acceptance" tail becomes moot for the service lanes).
Host-load rule: gates and live acceptance are one container at a time, `docker update --cpus=3` after launch, never on <protected-host>; real-docker work is on the r1002/v1001 hosts only; contention-agnostic assertions (no timing oracles; readiness by event, not sleep).

### Risks
1. Compose behaviour is the biggest unknown (WP-C0): `run -d` TTY/stdin handling, `container_name`/`hostname` of one-offs, one-off removal by `down --remove-orphans`, merge semantics of the extension fragment. The plan has fallbacks for the first and last; the spike is cheap and gates everything.
2. Hooks in `ciu run` have side effects (pre_compose identity cache, hostdir creation). Documented, `--skip-hooks` available, serialised by the prepare lock; concurrent runs rewrite the same cache with identical content.
3. Lost hardening: Run-Gate raw ephemeral adds a sanitized git-config overlay and a read-only `worktrees/` view; the dstdns service mounts the git common dir read-write (stated in its compose, "parity with main's runner"). A `[.ciu]` lane gets exec-mode parity, not ephemeral-mode hardening. Stated in the SPEC clause; fixable later by letting the extension carry those overlays (WP-R1 already builds the fragment machinery).
4. `ciu down`/stop is not ownership filtered (CIU-193): another checkout's `down` can stop a one-shot under shared deploy tags. Exposure window = lane duration; mitigated by `await_container`'s state check producing an INFRA result, not a verdict. CIU-193's fix is an independent item and should be scheduled in the same ciu release if cheap.
5. DooD siblings (PG, p165 `-runner` clone) are outside the observed container; R2 resource evidence undercounts. Disclosed, not fixable inside this plan.
6. Compose `run` ignores a service's `container_name` but keeps `hostname`: any consumer script using `hostname` as identity breaks (dstdns: fixed in WP-D1, and `CIU_RUN_CONTAINER_NAME` is the supported replacement).
7. CIU-176 (worktree render) unmerged; the one-shot in a worktree depends on it.
8. Registered-gate effort: new verb touches generated surface artefacts (`cli-surface.json`, SPEC S10.8 table, cli-review files); WP-C2 must regenerate and the CLI-surface completeness test must stay green.
9. dst185 branch (8bf2ce3c) is unmerged and edits the same lane blocks; WP-D1 must start from main after that merge to avoid add/add conflicts (integrate-by-third-version rule applies).

## 6. Open questions for the operator (max 6)

1. Foreground semantics: may `ciu run` foreground be implemented as detached-launch + attach + exact-name wait/remove (one code path, guaranteed status-before-remove), accepting small differences from plain `docker compose run --rm` (decided by the WP-C0 spike)? Recommendation: yes; fall back to plain `compose run --rm` + exact-name inspect watcher only if the spike shows attach breaks TTY/stdin, in which case observer-aware use stays `--detach`-only.
2. Is "ciu run --detach + Run-Gate keeps await/remove" acceptable as the CIU-141 "observer-aware" contract, instead of CIU owning the whole lifecycle with a callback channel? Recommendation: yes (smaller, reuses RG-35 re-attach and the tested `await_container`); the JSONL events channel still ships for foreground use and later v8 gate use.
3. First-instruction observer coverage (`--hold` readiness barrier, WP-X1): required for R2, or is "observer attaches right after start, exactly as the raw ephemeral path does today" enough for v1? Recommendation: v1 without the barrier (extra complexity: entrypoint wrapper, image needs a shell, original-entrypoint resolution); revisit only if an R2 lane's evidence shows missed early peaks.
4. Verb ownership vs v8: CLI-SURFACE-DECISIONS C13/CQ5 said `ciu run` is v8 and the `gate` group name stays reserved. Confirm that `ciu run` ships in v7 now and v8's `gate run`/`gate exec` delegates to it. Recommendation: yes; amend C13 in WP-C5 and keep `ciu gate` unregistered (C14 stands).
5. `sql-mutation` DSN delivery: keep the operator-exported password-bearing DSN via `forward_env` for v1, or declare the schema-gate-pg password as a CIU secret in the test-runner stack now? Recommendation: v1 = forward_env (today's behaviour, zero new secret plumbing); file the CIU-secret variant as a dstdns backlog item after the 10 lanes have real verdicts.
6. Lost git hardening (risk 3): accept exec-mode parity for `[.ciu]` environments in v1 (service mounts are authoritative), or require Run-Gate's sanitized git-config overlay through the extension from day one? Recommendation: accept parity in v1, record it in the SPEC clause, and add the overlay as a follow-up (the extension channel already supports it).
