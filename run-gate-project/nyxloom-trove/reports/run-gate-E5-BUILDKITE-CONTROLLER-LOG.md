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
