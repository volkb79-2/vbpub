# assay 3.1.0 — design review of the JavaScript/TypeScript adapter, first-consumer fit, and what to rule before the Go wave and CIU v8

**Reviewed:** assay-v3.1.0 (B036, `judge.language = "javascript"` at R1 +
`coverage-istanbul-json`), as released 2026-08-30 (`c0b0e182`).
**Reviewer:** Fable 5 (xhigh), fresh session, read-only pass over
`src/assay/adapters/javascript.py`, `src/assay/coverage_parsers/coverage_istanbul_json.py`,
`src/assay/isolation.py`, `src/assay/evaluate.py`, `src/assay/cli.py`,
`tests/test_cli_run_javascript.py`, `docs/*`, `4-backlog.md` B036–B040,
`decisions.md` A-340..A-346, `ciu/docs/SPEC-V8.md` S15–S16,
`ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §4.1.10/§4.3.8/§4.5 G/§4.10, the
v8 demo, and the first consumer (`dstdns/applications/webapp-ui-react`,
`dstdns/run-gate.toml`, `dstdns/tools/test-runner/Dockerfile`).
**Operator rulings taken during this review (2026-08-30):** recorded in §5.
**Filed out of this review:** assay B041–B048, ciu CIU-72/CIU-73; B037
resolved; B038/B039/B040 cross-referenced.

## 1. Verdict

Nothing shipped in 3.1.0 needs reverting, and no recorded decision
(A-340..A-346) should be reopened. The adapter's own claims were measured, and
the round-2 handling of the `@vitest/coverage-v8` defect (documentary ruling,
committed witnesses, no sniffing) is the right call.

The one real miss: **the adapter's real-run proof stopped at the snapshot
boundary.** Every real `vitest` artifact was produced *outside* assay, and the
end-to-end CLI test's lane command is a shell heredoc that writes the artifact
(`tests/test_cli_run_javascript.py:88,131`) — a test double for the producer,
which A-334 already names as "a recording of the belief under test". The first
consumer lives on the far side of that boundary: JavaScript keeps its dependency
closure *in-tree* (`node_modules`, gitignored), and assay's snapshot is
`git read-tree <commit>` into a fresh temp dir (`isolation.py:492,577`) — so a
JS lane's `npx vitest` inside the snapshot has no vitest. Python (venv) and Go
(`GOMODCACHE`) never met this because their closures are out-of-tree.

Three design rulings are cheap now and expensive after the Go wave lands:
declare the coverage **producer** (B045), make ingested R2 look exactly like R1
(B046, resolving B037), and put helper identity into the CIU v8 gate envelope
(CIU-72).

## 2. What is right — keep

| decision | evidence | why it holds |
|---|---|---|
| format ≠ language: 5th format + 4th language with zero core/protocol/registry edits | `coverage.py:105-120`, `registry.py`, REPORT §1 | the design bet paying off under load, not coincidence |
| one `"javascript"` for `.js/.jsx/.ts/.tsx` (A-340) | artifact carries no dialect field | the alternative forces two languages for one measurement |
| parser-side extent expansion, innermost wins, max-count ties (A-342) | `coverage_istanbul_json.py:289-305`; `branchy.ts` `[2,4]`=1 vs `[3,3]`=0 | recovers what Python needs an AST walk for, from the artifact; the "executed wins" alternative is a false green on a measured file |
| A-342 corrected by review: 23 unattributed signature/brace lines fall to rule 4 | `decisions.md` A-342 M2; docs in three places | the narrower guarantee is the true one; stated, not hidden |
| `excluded=None`, `branches=None` as measured refusals (A-343/A-344) | `hinted.ts` leaves no `skip`; producers disagree on `branchMap` | a number whose meaning depends on an undeclared fact is the `declared_unverified`-class lie |
| A-346 as a product ruling, not a sniffing guard | four committed artifacts, both Vitest majors | the artifact cannot distinguish a true count from a false one; sniffing is the A-007 collapse and would already have broken between 3.x and 4.x |
| R1-only registration (`cli.py:358-359`) | Python's own first-ship precedent | nothing over-claimed |
| absolute-key reconciliation in the core, not the adapter (A-341) | `evaluate._to_repo_relative_key` | not a language fact; checked in three places |

## 3. Gaps that bite the first consumer

### G1 (high) — no path for the dependency closure inside the snapshot → **B041**

- `isolation.py:577` `read-tree`; `isolation.py:492` `mkdtemp(prefix="assay-p22-snap-")`; the lane runs at `cwd=snapshot.project_root` (`runner.py:1754`).
- `tests/test_cli_run_javascript.py:88`: `argv = ["/bin/sh", "-c", <heredoc writing coverage-final.json>]`. No real `vitest` has ever run inside a snapshot.
- dstdns's runner image ships Node (`tools/test-runner/Dockerfile:17-43`) and `npm ci`s into the bind-mounted checkout at gate time; the snapshot has neither.
- `npx vitest` with no `node_modules` does not fail loudly: `npx` fetches a missing package from the registry (unpinned, network). `--no-install` makes it fail; nothing today tells a consumer that.
- `environment_command` (B010) cannot vouch for it — it runs in the *invoking* environment before snapshot work (DESIGN-GUIDE §4).
- R3 triples the cost (baseline + two canary runs, each a snapshot).

### G2 (medium) — the worked lane does not work for a monorepo app → **B042, B043**

`CONSUMERS.md:552-563` pairs `argv = ["npm", "run", "test:coverage"]` (repo
root — no root `package.json` in dstdns) with
`source_roots = ["applications/webapp-ui/src"]`. There is no lane-level `cwd`
(`config.py:139-155`); Vitest's `reportsDirectory` resolves against the app
root, so `artifact` must be `applications/webapp-ui-react/.assay/coverage-final.json`.

### G3 (medium) — "Jest is unaffected" is an overclaim → **B042**

`README.md:191`, `CONSUMERS.md:676-678`. Only Jest's *default* `babel`
provider shares the istanbul instrumenter; Jest `coverageProvider: "v8"` and
`c8` emit the same `coverage-final.json` through v8 remapping and were not
measured.

### G4/G5 (low) — consumer readiness

One type-only module in the React app (`src/auth/types.ts`) — B038(b) is live
but rare. Zero unit tests, no coverage provider in `package-lock.json`,
`vite.config.ts` imports `defineConfig` from `vite` (a `test:` block needs
`vitest/config`). Assay is ahead of the consumer: the dstdns package is
"provider + jsdom/testing-library + first component tests + lane", not a lane
alone. Listed under B041 "Consumer-side actions".

## 4. Rulings worth making before the Go wave

| id | what | why now |
|---|---|---|
| **B045** | `judge.coverage.producer`, declared and recorded (schema v9) | closes B038(a)(b) + B040(b); the Go carve needs the same key (`go test` vs `covdata textfmt`) |
| **B046** | ingested R2: lane argv runs Stryker in the snapshot; `judge.mutation.format = "mutation-report-json"` + `artifact`; `judgment.r2.producer = "ingested"` | resolves B037's three open questions with R1's own shape; no new trust boundary |
| **B043** | lane-level `cwd` (recorded) | retires the `bash -c "cd … &&"` wrapper that hides argv[0] from the `MISSING_EXTERNAL_TOOL` preflight |
| **B044** | `assay lanes --json` | lets `ciu check`/`ciu gate doctor` verify environment fitness and derive `request_base` without reading `assay.toml` |
| **B047** | Go wave prep: helper distribution/identity, `helpers[]` in the gate envelope, B039 bound, `covdata` path | A-239 settled the seam, not the helper's distribution |
| **B048** | browser e2e coverage via `vite-plugin-istanbul` *inside* the lane; the S3 limit is B004's | possible today with no assay change; the detached `assay judge` verb waits for B004 |
| **CIU-72** | LaneResult carries `helpers[]`; stage 12 consumes `assay lanes --json` | S16.9 copies only `judge_provenance` |
| **CIU-73** | demo/spec: a JS assay lane + closure caches on the tester environment | the model cannot express language needs today |

## 5. Operator rulings (2026-08-30, live interview)

1. **Schema:** one bundled **v9** cut ("producer wave", assay 4.0.0): coverage
   producer (R1), `judgment.r2.producer`, `cwd`, `link_paths`, helpers role —
   one major bump with migration notes; the Go wave reuses the keys.
2. **B037:** the proposed shape (lane argv runs Stryker; format-keyed mutation
   report registry; diff intersection; bucket map; `producer = "ingested"`) is
   **ratified**; B046 is dispatchable.
3. **G1:** **both** — the documented offline-install pattern + a real-vitest
   qualification, **and** a declared `isolation.link_paths` feature — with
   detailed consumer usage docs.
4. **CIU asks:** **both** — CIU-72/73 filed, and SPEC-V8.md / the demo
   annotated with short pointers at the affected sections.

## 6. Minor precision items (folded into B042)

- `*.stories.tsx`, `src/test/setup.ts`, `vitest.setup.ts`, `*.config.*` are not
  test paths by the adapter's rule; Vitest's own default `coverage.exclude`
  drops config files from the artifact, so a changed one under `source_roots`
  reads as uncovered (fail-closed, visible). Say "keep them out of
  `source_roots`".
- `.mjs/.cjs/.mts/.cts` absence is fine as recorded (one-line change when a
  consumer has one).

## 7. Consumer-side actions (dstdns) — ready to paste into a dstdns brief

Not filed in vbpub; dstdns owns them. Kept here so they are not lost.

1. `npm install --save-dev @vitest/coverage-istanbul jsdom @testing-library/react @testing-library/jest-dom`
   in `applications/webapp-ui-react` (commit the lockfile change).
2. `vite.config.ts`: `import { defineConfig } from 'vitest/config'`; add
   `test: { environment: 'jsdom', include: ['src/**/*.{test,spec}.{ts,tsx}'], coverage: { provider: 'istanbul', reporter: ['json'], reportsDirectory: '.assay', include: ['src/**'] } }`.
3. First component tests under `src/**/*.test.tsx` (the adapter's
   `is_test_path` rule; keep `src/test/setup.ts`-style support files out of
   `source_roots` or expect them judged).
4. Gitignore `applications/webapp-ui-react/.assay/`.
5. Bake an offline npm cache into `tools/test-runner` from the committed
   lockfile (B041 pattern) — or mount one — and write the lane per B041's
   worked example (`npm ci --offline …` first, `npx --no-install vitest run --coverage` second).
6. `assay.toml` lane `ui_unit`: `language = "javascript"`,
   `source_roots = ["applications/webapp-ui-react/src"]`,
   `format = "coverage-istanbul-json"`,
   `artifact = "applications/webapp-ui-react/.assay/coverage-final.json"`,
   `base_source = "request"` once the gate passes `--request-base`
   (run-gate N12 / ciu v8 S16.7); `require_branch` unset until B045.
7. `run-gate.toml` (today) / `[testing.lanes.ui-unit]` (v8): `kind = "assay"`,
   `environment = "test-runner"`, no `requires.services`.
8. `src/auth/types.ts` is type-only: give it one runtime export or keep it out
   of `source_roots` until B045 lands (B038(b)).
9. Later (B048): the `__coverage__` dump fixture in
   `tests/e2e/ui/webapp-ui-react/conftest.py` and the `vite build --mode coverage`
   lane.
