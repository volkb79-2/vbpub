# dstdns — adopting assay 4.0.0 and the first JavaScript lane

**Written 2026-09-01 by the assay controller, for dstdns's controller.** This
is the consumer-side brief that `assay-3.1-js-adapter-design-review-2026-08-30.md`
§7 said was owed and that was never written. dstdns owns every action here;
assay owns nothing below. A copy sits in dstdns's gitignored `.assay-inbox/`
beside the 4.0.0 `release.json`, so it dirties no tree.

## Where dstdns stands, measured 2026-09-01

| fact | value |
|---|---|
| assay pin in `run-gate.toml` | `tools/assay/assay-3.1.0.pyz` (five lanes) |
| releases since | 3.2.0 (2026-08-30), 4.0.0 (2026-08-31), both notified via `.assay-inbox/release.json` |
| dstdns disposition so far | "3.2.0 adoption deferred until P152/P154/P161 close" (`CONTROLLER-BRIEF.md`, 2026-08-31); 4.0.0 unacted |
| `javascript` lanes in `assay.toml` | none (all lanes are `python` or `sql`) |
| React UI tests today | `[lanes.frontend-unit]` (run-gate `kind = "command"`, dstdns-P150b): `npm ci && npm run typecheck && npx vitest run` over three enumerated files in `vitest.config.ts` |
| coverage provider installed | none (`vitest ^3.2.4`, `jsdom`, `@testing-library/react` present; no `@vitest/coverage-*`) |
| run-gate version | 23.2.2 (RG-25 toolchain fitness and RG-26 `--base` passthrough shipped in 23.1.0) |

Waves A and B (3.2.0 and 4.0.0) were built for this UI. Until a `javascript`
lane exists, none of that work produces a verdict anywhere.

## Step 1 — repin straight to 4.0.0 (skip 3.2.0)

Take the `sha256` and URL from `.assay-inbox/release.json` (4.0.0). Re-vendor
the `.pyz`, update the five `assay_command`/`pins.assay` blocks in
`run-gate.toml`, re-run the gate once. What the 4.0.0 cut means for the lanes
dstdns already has:

- **Verdict schema v8 → v9 is a hard cut.** `assay verify` at 4.0.0 refuses a
  v8 document. Any archived verdicts you compare against must be re-emitted;
  nothing else changes for `python`/`sql` lanes.
- `producer` is REQUIRED only for `coverage-istanbul-json` lanes (none yet).
  `coverage-py-json` accepts it optionally and ignores its absence.
- `judge_provenance` and `--require-judge-provenance` (3.0.0) are unchanged;
  `judge.base_source = "request"` (3.0.0) is now reachable from the gate, see
  step 3.
- Migration notes: `vbpub/assay/docs/CONSUMERS.md`, section "Migration notes
  (v8 → v9)".

Do this as its own small package. It is mechanical and it unblocks step 2.

## Step 2 — the first `javascript` lane: `ui_unit`

Everything below is in `vbpub/assay/docs/CONSUMERS.md` §"JavaScript/TypeScript
lanes" (line ~584 at 4.0.0), which is the authoritative text; this is the
dstdns-shaped summary.

1. **Provider.** In `applications/webapp-ui-react`:
   `npm install --save-dev @vitest/coverage-istanbul`, commit the lockfile.
   Never `@vitest/coverage-v8`: it reports never-executed lines as executed
   after a ternary in the same block, on both current Vitest majors, and assay
   refuses it by name (assay A-346, B040).
2. **`vitest.config.ts`.** Keep the enumerated `include` list exactly as it is
   (D-234(b), asserted by `test_frontend_unit_lane_declaration.py`). Add:
   ```ts
   coverage: {
     provider: 'istanbul',
     reporter: ['json'],          // writes coverage-final.json
     reportsDirectory: '.assay',
     include: ['src/**'],
     clean: false,                // REQUIRED: assay B049, see below
   },
   ```
   `clean: false` is not a style choice. Vitest's default deletes and
   recreates `reportsDirectory` under the directory handle assay already
   holds, and the lane then reads `NO_MEASUREMENT/EMPTY_COVERAGE` over a
   complete artifact. Measured in Wave A; the assay-side fix (B049) is filed,
   not shipped.
3. **Gitignore** `applications/webapp-ui-react/.assay/`.
4. **Dependency closure.** A snapshot is `git read-tree` of committed objects,
   so `node_modules` does not exist inside it. Two supported shapes; pick (a)
   now:
   - (a) `link_paths = ["applications/webapp-ui-react/node_modules"]`
     (4.0.0, B041(b)): the checkout's installed tree is symlinked into every
     snapshot; the verdict records `snapshot_policy.link_paths` so nobody
     mistakes it for commit-bound. Works today because `frontend-unit`
     already runs `npm ci` in the same checkout.
   - (b) offline `npm ci --offline --cache <baked cache>` inside the lane
     (3.2.0, B041(a)): commit-bound, needs a cache baked into `test-runner`
     from the lockfile. This is the v8 shape (ciu CIU-73 `extra_mounts`).
5. **`cwd`.** `cwd = "applications/webapp-ui-react"` (4.0.0, B043) so the argv
   is a bare `npx --no-install vitest run --coverage`, no `bash -c cd`
   wrapper. A lane whose `cwd` and a `link_paths` entry name the SAME path is
   refused at load (the Wave B blocker); a `link_paths` entry nested under
   `cwd`, as here, is the ordinary case.
6. **The lane**, matching dstdns's existing lane style:
   ```toml
   [lanes.ui_unit]
   scope = "S1"
   rigor = ["R0", "R1"]
   enforcement = "gate"
   cwd = "applications/webapp-ui-react"
   argv = ["npx", "--no-install", "vitest", "run", "--coverage"]
   env_passthrough = ["PATH", "HOME"]
   budget = "10m"
   allow_argv_append = false

   [lanes.ui_unit.isolation]
   snapshot_selection = "repository"
   link_paths = ["applications/webapp-ui-react/node_modules"]

   [lanes.ui_unit.judge]
   language = "javascript"
   source_roots = ["applications/webapp-ui-react/src"]
   mode = "changed_lines"
   fail_under = 100.0
   allow_excluded = false
   require_branch = true          # real istanbul branch arcs since 4.0.0 (B045)
   base = "<commit>"              # or base_source = "request", step 3

   [lanes.ui_unit.judge.coverage]
   format = "coverage-istanbul-json"
   producer = "istanbul"          # REQUIRED for this format at 4.0.0
   artifact = "applications/webapp-ui-react/.assay/coverage-final.json"
   ```
   `src/auth/types.ts` (type-only) needs no special handling any more:
   4.0.0's type-only lexer (B045, B038(b)) excludes it rather than judging
   it as uncovered.
7. **`run-gate.toml`**: a sixth `kind = "assay"` lane, `environment =
   "test-runner"`, `assay_lane = "ui_unit"`, the 4.0.0 pin. `run-gate doctor`
   (23.1.0+, RG-25) checks that `node` resolves on PATH inside
   `test-runner`; the image copies Node into `/opt/node`, so make sure that
   is on the environment's PATH, not only the Dockerfile's.

Adding coverage to `frontend-unit`'s own command is not the same thing: that
lane reports pass/fail of the test run; `ui_unit` judges the changed lines.
Keep both.

## Step 3 — optional, now usable: base from the gate request

`judge.base_source = "request"` (3.0.0, B019) lets the gate supply the
comparison base instead of a hard-coded commit in `assay.toml`. run-gate
23.1.0 (RG-26) passes `run-gate <lane> --base REF` through as
`--request-base`, deriving the delegating lanes from `assay lanes --json`.
Declaring both `base` and `base_source` is refused. Worth switching the
existing Python lanes' pinned `base = "<sha>"` values to this once the
repin lands; in ciu v8 this becomes `request_base = true` on the lane.

## Later, not now

- **R2 for the UI** by ingestion (4.0.0, B046): the lane's own argv runs
  StrykerJS inside the snapshot, `judge.mutation.format =
  "mutation-report-json"`. `fail_under` must be `100.0` at 4.0.0 (B050 is
  the filed gap). Only after `ui_unit` is green for a while.
- **Browser coverage** of the served UI (B048): `vite-plugin-istanbul`
  inside the lane, `producer = "istanbul"`. Documented, no assay change
  needed; not before dstdns wants an S3 UI lane judged.

## Pointers

- `vbpub/assay/docs/CONSUMERS.md`: "JavaScript/TypeScript lanes" (~584),
  "JavaScript lanes and the dependency closure" (~657), "Linking a dependency
  closure into the snapshot: `link_paths`" (~773), "Declaring the coverage
  producer (B045)" (~892), "Run the lane somewhere other than the project
  root: `cwd`" (~86), "R2 for JavaScript, by ingesting Stryker's report"
  (~1098), "Migration notes (v8 → v9)".
- `vbpub/assay/nyxloom-trove/reports/assay-3.1-js-adapter-design-review-2026-08-30.md`
  §7: the original action list this brief updates.
- `.assay-inbox/release.json` (4.0.0): the sha256 and the one-paragraph
  release note.
