# E-5 — remote and asynchronous lanes on Buildkite — controller log

Plan of record: `assay/nyxloom-trove/WAVE-PLAN-2026-09-02-after-v10.md` §4b
(operator, 2026-09-02: "Buildkite on your own hosts is the next step").
Manual: `run-gate-project/REMOTE-LANES-BUILDKITE.md`. Decision of record:
dstdns D-110.4. Rulings here are E5-Rn.

- **2026-09-03 (seams 2 and 4 landed on `feature/run-gate-buildkite-seams`;
  E5-R1..E5-R6; one follow-up before review)** — Fresh implementer, three
  commits on top of main: `c07ae6af` `tools/buildkite/pipeline.sh` (reads
  `./run-gate.py --list` only — a test pins that `run-gate.toml` appears
  nowhere in it — and emits one §3 command step per selected lane; refuses
  by name a missing `RUN_GATE_QUEUE`, an unknown `RUN_GATE_LANES` name, a bad
  timeout, a listing wider than three columns), `8fcf3dd9`
  `tools/buildkite/bk-lane.sh` (`run|status|collect`, token file must be
  0600, `curl` + `python3` only, `--dry-run` prints every curl with the
  bearer redacted and makes no network call), `81ff037f` docs (§3/§4/§6 name
  the real files; the artifacts endpoint verified against the vendor docs
  that day: `GET /v2/organizations/{org}/pipelines/{pipeline}/builds/{number}/artifacts`,
  collector uses `path` + `download_url`). Verified by the controller:
  selftest on `81ff037f` `452 passed, 2 skipped` / `diff-coverage OK: 0/0`
  (the floor is scoped to `run-gate.py`, which this branch does not touch;
  the 35 new tests ARE collected by the lane) / `lane 'selftest' exit 0`
  (`selftest.log`, read separately); tree clean; touches none of the files
  the 23.4.0 wave edits; `shellcheck -S warning` clean on both scripts.
  **Nothing live was run** — no build, no download — and the docs say so.
  - **E5-R1 (ask 1 — remote-capable metadata in `--list`): NOT a fourth
    column.** The three-column listing is the stable consumer contract
    (CONSUMERS anti-goals); widening it breaks every parser at once. Later:
    **RG-45** — a lane key (`remote = true`) exposed through a new
    `--list --json` form; `pipeline.sh` selects remote-capable lanes when
    `RUN_GATE_LANES` is empty once that exists. Until then "empty selects
    every lane" stands, documented.
  - **E5-R2 (ask 2 — `blocked` is terminal, exit 1): confirmed.** No manual
    unblock step exists in our pipeline; `--wait-blocked` is a future flag
    if one ever does.
  - **E5-R3 (ask 3 — `collect <commit>`): follow-up, RG-46**, filed with the
    history `host` field (RG-43) and the dstdns `.assay-inbox/` hand-off;
    needs the build-search endpoint verified first.
  - **E5-R4 (ask 4 — unfiled rows): the controller files RG-41..RG-46 after
    the 23.4.0 merge** (append-conflict avoidance with RG-39/RG-40 on the
    wave branch): RG-41 lane-authoring guide row, RG-42 `image_digest` in
    the environment table, RG-43 history `host` field, RG-44 `doctor` names
    an unowned container wearing a lane name (RW-22), RG-45 remote-capable
    lane metadata + `--list --json`, RG-46 collect-by-commit + inbox
    hand-off.
  - **E5-R5 (ask 5 — §6 row 1 wording): accepted as qualified** — row 1 is
    a docs seam whose only executable evidence is the generator's
    `artifact_paths`.
  - **E5-R6 (ask 6 — queue placement): pipeline-level `env.RUN_GATE_QUEUE`
    stays the default; a build's env overrides it** (Buildkite build env
    takes precedence). Follow-up sent to the same implementer: `bk-lane.sh
    run` gains optional `BK_QUEUE` → `env.RUN_GATE_QUEUE` in the
    create-build body, with dry-run tests and two doc lines; then a short
    fresh review (dry-run correctness, doc truth, no network, no touched
    wave files), then merge `--no-ff` to main — no release, the next
    run-gate release carries the tools; the CHANGES line is folded after
    23.4.0 lands.

- **2026-09-03 (E5-R6 follow-up landed at `17077426`; reviewer round 1
  dispatched)** — `bk-lane.sh run` gains optional `BK_QUEUE` → sent as
  `env.RUN_GATE_QUEUE` in the create-build body only when set (an empty
  key would override the pipeline's default with nothing); a value outside
  `[A-Za-z0-9._-]` refused by name; three dry-run tests (38 total, serial
  under nice/ionice, 1.09 s); §3 and §4.2 say a build's env overrides the
  pipeline queue. No selftest for this commit, deliberately and stated: no
  `run-gate.py` line changes, the lane's diff-coverage floor has nothing
  new to judge. Implementer's correction accepted: `shellcheck` had emitted
  one info-level SC1003 false positive on `pipeline.sh` line 93 (a literal
  backslash in a `case` pattern), now disabled by name with the reason
  inline — the controller's "shellcheck clean" was at `-S warning`, which
  hides info-level notes; both scripts are clean at every level now.
  Reviewer round 1 dispatched (fresh Opus, never a fork) on `17077426`:
  blind diff, scope discipline, the generator against three real projects
  parsed as YAML, dry-run transcripts checked against the vendor REST docs,
  no-network proof, the 38 tests + hollow-test hunt, docs-truth table,
  shellcheck + shell-safety; report committed as its only file. After
  ACCEPT: merge `--no-ff` to main, no release.

- **2026-09-03 (reviewer round 1: NOT ACCEPT — 1 BLOCKER, 5 SHOULD-FIX,
  9 NIT; E5-R7..E5-R12; fix package to the same implementer, round 2
  next)** — Report `31efccca` on the branch. Verified true by the reviewer:
  scope discipline exact; §3 step shape validated key-by-key and in order
  via PyYAML against run-gate-project (1 step), cmru (5), assay (1); every
  vendor REST path, artifact field and build state matches the fetched
  docs; E5-R6's premise confirmed by the vendor (precedence Pipeline →
  Build → Step); zero socket syscalls under `strace -f -e trace=network`
  for all three dry-run verbs; token never printed; 38/38 tests, collected
  by the selftest lane. Findings: **B1** `collect` used the build
  response's free-form `commit` as a directory name unchecked (a
  `../../../escape` commit wrote outside `<dir>` under a stubbed curl);
  **S1** `run --dry-run lint` (flag after the verb) is swallowed as a lane
  name and POSTs a real build; **S2** §6 row 1 claimed "landed" for a seam
  nothing declares, and "matching" globs the generator cannot compute;
  **S3** unbounded polling; **S4** the stored pipeline snippet pipes the
  generator into the upload with no pipefail and no queue on the upload
  step; **S5** refusals exit 1, not the stated 2; N1 the whole non-dry-run
  path untested (E5-R2 "asserted" by a word in prose); N2–N9 as listed.
  - **E5-R7 (B1 + N1):** shell guard `[A-Za-z0-9._-]+` on `commit` and
    `[0-9]+` on the build number before either is a path component; AND
    the PATH-stubbed `curl` harness so the traversal guard, the seven
    terminal states and the exit-code contract are genuinely tested.
  - **E5-R8 (S2, ask 2 → (a) now):** §6 row 1 returns to a true state
    (docs seam, not started; fixed globs are the only executable evidence;
    artifacts outside `.assay/` and `.run-gate/` do not travel); the rule
    "a remote-capable lane keeps every artifact it wants back under
    `.assay/`" added by the controller to LANE-AUTHORING.md §5 on main;
    (c) — `artifacts` via `--list --json` — folds into RG-45.
  - **E5-R9 (S1, ask 3 → both):** `--dry-run` accepted anywhere in argv;
    `-`-leading lane names refused by name.
  - **E5-R10 (S3, ask 4 → now):** `BK_MAX_WAIT_MINUTES` (default 300),
    exit 3 naming build number and last state when exceeded.
  - **E5-R11 (S4, ask 5 → fix here):** generate to a file, then upload it
    (no pipefail subtlety); the upload step declares a LITERAL enrollment
    queue with the reason in one sentence.
  - **E5-R12 (S5):** exit codes 0 passed / 1 not passed / 2 refused (every
    refusal through `die`) / 3 gave up waiting, stated once, tested.
  - **Nits N2–N9 all taken** (N6 → refuse duplicate lane names; N8 → third
    glob `<project>/.assay/*` AND §4.3 softened until the first live build).
  - Fix package sent to the same implementer (context intact, small
    package); reviewer round 2 (same reviewer session, cap 3) on its tip;
    after ACCEPT merge `--no-ff` to main, no release.

- **2026-09-03 (round-1 fix package landed at `6dceaaf0`; E5-R13..E5-R15;
  one follow-up, then round 2)** — `a777f109` scripts + tests, `6dceaaf0`
  docs. Reported: 65 tests (was 38), green in declared and random order,
  484 collected across `tests/`; `shellcheck -S style` clean; scope
  diff against every wave-owned file empty. E5-R7: `check_path_component`
  gates the response `commit` (`[A-Za-z0-9._-]`, no `.`/`..`) and the build
  number before `mkdir`; the PATH-stub `curl`+`sleep` harness makes the
  live path real in tests (seven terminal states with actual exit codes,
  the commit guard, three escaping artifact paths, malformed/short
  responses, a happy-path `collect` landing bytes at
  `<dir>/<commit>/.assay/verdict.json`). E5-R9: `--dry-run` anywhere
  (`--` ends options), `run --dry-run lint` records zero curl calls,
  `-`-leading names refused. E5-R10: `BK_MAX_WAIT_MINUTES` → exit 3 naming
  build and last state. E5-R12: every refusal through `die`, contract
  0/1/2/3 stated once. E5-R8/E5-R11 and N2–N9 as ruled.
  - **E5-R13 (ask 1):** N9 has no meaning for `run` — accepted as reasoned.
  - **E5-R14 (ask 2):** keep the sleep-time budget as documented AND bound
    each request with `--connect-timeout 10 --max-time 120`; the doc says
    the wall-clock bound is the budget plus at most one request per poll.
  - **E5-R15 (ask 3):** the `.assay/` authoring rule landed on main
    (`3f148522`); §3 and §6 row 1 point at LANE-AUTHORING §5; row 1 stays
    "not started". Round 2 (same reviewer, cap 3) on the follow-up's tip.
