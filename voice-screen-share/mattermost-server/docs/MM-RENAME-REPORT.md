# MM-RENAME report

Branch `mm-rename` (worktree `/workspaces/vbpub/.worktrees/mm-rename`), base `main` at `c950c45b8`.
Not merged, not pushed. Everything below was run unless marked INFERENCE. No ciu command was run
(only `ciu worktree create mm-rename --base main`, which the order prescribed). The live stack and its
ignored files in the main checkout were not touched; the only look at them was one read-only
`git status --short --ignored mattermost` to list ignored file names.

Code commit: `3ef40d9ff` (rename + references + guard). The report commit sits on top and touches only this file.

## What changed
`git mv mattermost mattermost-server` (all tracked files, 100% renames) plus the reference edits below.
Only the project FOLDER changed. Unchanged by design: ciu stack/service name `mattermost`, service keys,
`[mattermost.secrets]`, `GEN_LOCAL:mattermost/<name>`, the store subpath `.ciu/secrets/mattermost/<name>`,
`deploy.project_name = "mattermost"`, container/hostname `mattermost...`, the image `mattermost/mattermost-team-edition`,
the container-internal `/mattermost/{config,logs,bin}` paths.

## Reference table
Command: `git grep -n -E '(^|[^-a-z_./])mattermost/|(\.\./|vbpub>?/|--dir |root-folder )mattermost([^-]|$)|\{worktree\}/mattermost'`
with `CHANGES.md`, `*KNOWN_ISSUES*`, `nyxloom-trove/reports/*` and `nyxloom-trove/backlog/*` excluded (listed as left below),
plus a folder-style sweep (`mattermost/(README|CONSUMER|hooks|tools|ciu|run-gate|tests|vol|docker|docs)`, `<vbpub>/mattermost`).

### Updated
| File | What | Why |
|---|---|---|
| `mattermost-server/docker-compose.yml` | 7 absolute `/home/vb/volkb79-2/vbpub/mattermost` (3 binds, postgres secret `file:`, 3 comment recipes) and header `--dir mattermost` | functional: plain-compose fallback binds absolute host paths |
| `mattermost-server/run-gate.toml` | lane argv `cd {worktree}/mattermost` | functional: the `suite` lane would not find the folder |
| `mattermost-server/README.md` | `--root-folder /workspaces/vbpub/mattermost` (all), `/home/vb/volkb79-2/vbpub/mattermost` (all), `--dir mattermost` (all), relative `mattermost/.ciu/secrets/` (all), `mattermost/ciu.defaults.toml.j2` x2, `mattermost/ciu.compose.yml` x2; new "Folder name (MM-RENAME)" paragraph | runbook commands must work |
| `mattermost-server/CONSUMER.md` | 2 secret paths `mattermost/.ciu/secrets/...` | consumer contract |
| `mattermost-server/ciu.defaults.toml.j2:462` | comment secret path | text only |
| `mattermost-server/tests/test_stack_paths.py` | rewritten: pins `mattermost-server`; new guard (step 3) | the path guard |
| `mattermost-server/tests/test_mattermost_provision_hook.py` (2), `test_mattermost_reachability.py` (1) | docstring paths | text only |
| `nyxloom/nyxloom-trove/nyxloom.toml` | 3 `cat mattermost/.ciu/secrets/...` (comment recipes), 2 doc paths, 1 `<vbpub>/mattermost` | comments, so unobserved by gates |
| `nyxloom/src/nyxloom/decision_chat.py:43`, `intake_bridge.py:31,38,427` | docstring/comment paths only | text only; touched src, so `tester-unified` was run |
| `nyxloom/ciu.global.defaults.toml.j2:34`, `nyxloom/cmru.toml:135-136`, `nyxloom/docs/ARCHITECTURE.md:240`, `nyxloom/ntfy/README.md:4` | folder path / relative link | living docs and comments |
| `scripts/netcup/README.md` (201 link text+target, 214 secret path), `DESIGN-GUIDE.md:75` | consumer contract paths | consumer contract |
| `docs/CONSUMERS.md:157` | secret path | consumer contract |
| `scripts/debian-install-v2/README.md:305`, `TODO.md:83`, `debian_install_v2/notify.py:7` | CONSUMER.md path / folder | consumer doc / docstring |
| `TESTING-ESTATE-CHECKLIST.md:106` | row label and link | living checklist |

### Left (with reason)
| File / hit | Reason |
|---|---|
| `mattermost-server/README.md:75-79`, `ciu.defaults.toml.j2` `GEN_LOCAL:mattermost/<name>` (6), `docker-compose.yml:129`, `ciu.defaults.toml.j2:110` image `mattermost/mattermost-team-edition` | stack name / project-store secret subpath / docker image, not the folder |
| `mattermost-server/ciu.global.defaults.toml.j2:4` and the README "Location" paragraph, test docstring, cmru.toml comment (`nyxloom/mattermost`, `<vbpub>/mattermost` as the former path) | deliberate history of the earlier locations |
| `docs/CONSUMERS.md:156`, `scripts/netcup/README.md:215` `NOTIFY_BACKEND=mattermost` | backend value, not a path |
| `nyxloom/CHANGES.md`, `ciu/CHANGES.md`, `nyxloom/nyxloom-trove/reports/*`, `nyxloom/nyxloom-trove/backlog/*`, `nyxloom/nyxloom-trove/reports/MM-MOVE-REPORT.md` | dated history |
| `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` (CIU-102, CIU-133, others), `cmru/KNOWN_ISSUES_TODO_BACKLOG.md:1746`, `ciu/docs/SPEC-V8.md:1162`, `ciu/src/ciu/secrets/materialize.py:323` (`pwmcp/mattermost layout`), `ciu/tests/.../test_ciu_materialize_self_root_lock.py` | other tools' dated records / generic layout description; not the project path |
| `nyxloom/nyxloom-trove/4-backlog-inbox.md:616`, `nyxloom/docs/design-context-lifecycle-experiments.md`, `nyxloom/docs/CLI-REFERENCE.md` | history / no folder path |
| `libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md` | grep: names no `mattermost` folder path, nothing to change |
| `.gitignore` | grep: no pattern names the folder (`**/.ciu/`, `vol-*/`, `ciu.instance.generated.toml`, `**/ciu.toml` are generic) |
| `nyxloom/src/nyxloom/config.py`, `nyxloom/tests/*`, `nyxloom-config.schema.json`, `scripts/debian-install-v2/**` (`mattermost_*` keys, backend name), `known-shape.json` | backend/field names, not paths |
| `docker-compose.yml` `container_name: nyxloom-mattermost[-db]`, traefik router `nyxloom-mattermost` | unchanged per MM-MOVE report; fixed names, not the folder |

A final re-grep (same pattern, minus the stack-name exclusions) leaves only the intentional rows above.

## Path guard (step 3)
`mattermost-server/tests/test_stack_paths.py` now: pins `STACK.name == "mattermost-server"`; keeps the old `nyxloom/mattermost`
check; adds `test_stack_files_do_not_name_the_pre_rename_project_path` over 9 files (the 5 stack files plus
`ciu.global.defaults.toml.j2`, `README.md`, `CONSUMER.md`, `run-gate.toml`) with regex `vbpub>?/mattermost(?![\w-])` and
`\{worktree\}/mattermost(?![\w-])`; adds a self-test that the pattern accepts `vbpub/mattermost-server`, `<vbpub>/mattermost-server`,
`.ciu/secrets/mattermost/<name>`, `GEN_LOCAL:mattermost/<name>`, `mattermost-server/.ciu/secrets/mattermost/<name>`, the image name and
`{worktree}/mattermost-server`, and rejects the old forms. The two absolute-path tests now require `{"mattermost-server"}` and parent name
`mattermost-server`. 19 tests in the file.

### Plant evidence
In `docker-compose.yml` one bind (`vol-mattermost-logs`) reverted to `/home/vb/volkb79-2/vbpub/mattermost/vol-mattermost-logs`.
`python3 -m pytest tests/test_stack_paths.py` (plain pytest, not the gate lane):
```
FAILED ...::test_stack_files_do_not_name_the_pre_rename_project_path[docker-compose.yml]
FAILED ...::test_compose_absolute_paths_stay_inside_the_mattermost_server_dir
FAILED ...::test_compose_bind_mounts_are_the_three_stack_hostdirs
3 failed, 16 passed
```
The new check failed (as did the two pre-existing path tests). Reverted by Edit; same file then `19 passed`. Before the plant: `19 passed`.
No plant was run against the stack-name/secret-subpath false-positive direction on the real files beyond the self-test (the real
files contain those strings and pass).

## Gates
`/proc/pressure/cpu` some avg10 was 1.3 to 3.7 before runs. Each run foreground under
`flock .../gate.lock nice -n 19 ionice -c 3`.

| Command | EXIT | Verdict |
|---|---|---|
| `cd mattermost-server && ./run-gate.py --worktree /workspaces/vbpub/.worktrees/mm-rename suite` (dirty tree, before commit) | 0 | `lane 'suite' verdict PASS; exit_code 0`; `143 passed`; coverage TOTAL 83% (reported only) |
| `cd nyxloom && ./run-gate.py --worktree ... tester-unified` (first try, dirty tree) | 3 | `NOT_RUN; reason dirty-tree` (run-gate refuses a dirty tree; no tests ran) |
| same, after commit `3ef40d9ff` | 0 | `tester-unified: PASS (exit 0)`; `lane 'tester-unified' verdict PASS; exit_code 0`; verdict artifact `nyxloom/.assay/verdict-tester-unified.json` |

The `suite` lane ran on the uncommitted tree (identical content to `3ef40d9ff`); it was not rerun after the commit. Logs in the scratchpad:
`mm-rename-suite.log`, `mm-rename-nyx.log` (the latter is the second run).

## Cutover expectations (INFERENCE from ciu source; nothing was run)
Paths are relative to `ciu/src/ciu/`. "Root" below means the ciu root = stack dir = the project folder, because the project is a
standalone root with the stack files in the root dir (MM-MOVE).

1. **Container names: unchanged prefix. Compose project: CHANGES.**
   - Container names come from the template, `mattermost-server/ciu.compose.yml.j2:18,70`, `container_name: {{ mattermost.container_prefix }}-mattermost[-db]`, with
     `container_prefix = {deploy.project_name}-{deploy.environment_tag}`; `project_name = "mattermost"` is a literal in
     `ciu.global.defaults.toml.j2`, not derived from the folder. So containers stay `mattermost-<ID>-mattermost[-db]`, NOT `mattermost-server-<id>-...`
     (but see item 2: `<ID>` changes).
   - The compose project is `{project}-{env_tag}-{Path(stack_dir).name}` (`engine.py:948-967`, `compose_project_name`). The stack dir basename is now
     `mattermost-server`, so the project becomes `mattermost-<ID>-mattermost-server` (was `mattermost-<oldID>-mattermost`).
   - Consequence (INFERENCE): the compose template declares no `name:` for its named volumes (`ciu.compose.yml.j2:373` onward; only the ingress network has `name:`), so
     named volumes `mattermost-data`, `mattermost-plugins`, `mattermost-client-plugins` and the private bridge `..._internal` are prefixed by the compose
     project and will be NEW (empty) after cutover. The old ones are orphaned, not migrated. The operator already wants a clean instance, so this matches the MM-MOVE plan.
2. **Instance identity does NOT survive a rename of the root; ciu derives a new one.**
   - `INSTANCE_ID` is `workspace_id_for_path(physical_root)`, the first 6 base-36 chars of sha256 over the lexically normalised absolute path (`libraries/worktree/src/worktree/core.py:322-332`; used by
     `workspace_env.py:1894` and `:795-806`). A different path gives a different id, so `xm3foy` will not recur. `REPO_NAME` is `physical_root.name.lower()` (`workspace_env.py:791`),
     so it becomes `mattermost-server`, and the instance network (`{repo_name}-{suffix}-network`, `:804`) becomes `mattermost-server-<newID>-network`.
   - The stored record is not reconciled against the actual path. `_validated_identity_table` (`workspace_env.py:1401-1424`) checks key presence and string type only; I found no
     stored-vs-actual-root comparison in `workspace_env.py` (grep for moved/relocated/mismatch found only the ambient-env check at `:824`).
     `seed_identity_env` (`:1770`) OVERRIDES the process env from the stored record, including `REPO_ROOT` and `PHYSICAL_REPO_ROOT` (`GENERATED_FACT_ENV_KEYS`, `:127-134`). So a
     `ciu.instance.generated.toml` carried over would keep the OLD id and old `repo_root`/`physical_repo_root`; the S1.2 guard (`:354-381`) compares `REPO_ROOT` with the standalone root and
     would likely raise "REPO_ROOT does not match". Do not carry it over; let `ciu env generate --root-folder /workspaces/vbpub/mattermost-server` write a fresh one (the README
     Deploy block already says this).
   - This is the same reasoning as CIU-104's physical-root identity: identity is a function of the physical path, not of file content.
3. **Ignored files: which go with the folder.** The live folder holds these ignored entries (listed read-only): `.ciu/`, `ciu.compose.yml`, `ciu.env`, `ciu.global.toml`,
   `ciu.instance.generated.toml`, `ciu.toml`, `hooks/__pycache__/`, `vol-postgres-data/` (root-owned, unreadable to this user), `vol-mattermost-config/`, `vol-mattermost-logs/`.
   - Rendered/derived, REGENERATE, do not move: `ciu.toml`, `ciu.global.toml`, `ciu.compose.yml` (they embed the old absolute paths and the old id), `ciu.env`,
     `ciu.instance.generated.toml` (item 2), `hooks/__pycache__/`.
   - State, move ONLY if data should survive (the operator said none should): `vol-postgres-data/`, `vol-mattermost-config/`, `vol-mattermost-logs/` and `.ciu/secrets/**`
     (minted secrets: webhook URLs, PATs, `mattermost/<name>` passwords). A same-filesystem `mv` of the whole folder would carry them, but then the id and
     the compose project change anyway (items 1-2), so the old Postgres data would be reachable only via the bind path, and the secrets would be stale against fresh users.
   - Since the merge makes the tracked tree `mattermost-server/` while the old `mattermost/` keeps only ignored files, the cleanest order (INFERENCE) is: stop and remove the old
     containers by exact name (`mattermost-xm3foy-mattermost`, `-db`), archive the old `mattermost/` ignored files out of the way, then `ciu env generate --root-folder /workspaces/vbpub/mattermost-server`
     and `ciu up --dir mattermost-server -y`. As in the MM-MOVE report, the Traefik router `nyxloom-mattermost` and the public Host rule are unchanged, so two live copies would contend for one route.
4. **Consumers must be re-pointed anyway:** secret paths now `mattermost-server/.ciu/secrets/...` (updated in the docs above); the new instance mints new webhook ids/PATs.
5. **Not verified at all:** whether `--dir mattermost-server` itself needs the folder to be listed anywhere (the root declares no profile, per MM-MOVE); how `ciu` treats an
   unreachable old identity network on the first run; the project-store secret subpath after a fresh generate (MM-MOVE's own open INFERENCE, still open).

## Limits
- No ciu render/up/dry-run was run, so no derived name above was observed.
- The `suite` lane was run before the commit, `tester-unified` after it; the report file was added after both runs.
- The mattermost lane reports coverage but does not enforce it (unchanged from MM-MOVE).
