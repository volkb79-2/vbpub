# rigor-record

Stdlib-only interchange layer for the vbpub **rigor-evidence record** (design memo
`RIGOR-EVIDENCE-DESIGN-MEMO-2026-11-05`, package RR-1). It owns:

| Piece | Where |
|---|---|
| Normative specification | [SPEC.md](SPEC.md) |
| JSON Schemas (`rigor-fragment/1`, `rigor-record/1`, `rigor-waivers/1`, shared definitions) | `src/rigor_record/schemas/` |
| Schema and semantic validator | `rigor_record.validate`, `rigor_record.schema` |
| Distribution object, nearest-rank percentiles, normalisation | `rigor_record.stats` |
| Privacy lint | `rigor_record.privacy` |
| `assemble` (fragments + review + build inventory -> release-time record and body), `render` (record, body, `index.json`, `RIGOR.md`), `check` (offline drift check) | `rigor_record.assemble`, `.render`, `.check`, `.cli` |
| `sync` (release asset -> record on main, sha256-checked), `update` (R2 fragments, qualifications, reconcile, withdraw), the git write procedure | `rigor_record.sync`, `.update`, `.store` |
| Golden fixtures (fragments, one record per R2 status, waivers, percentile vectors) | `golden/` |

Producers (assay, run-gate) do not import this wheel at run time. They vendor the schemas and the
golden fixtures byte-identically into their tests and validate their own output against them.

```python
from rigor_record import validate_fragment, validate_record, lint, distribution

violations = validate_fragment(json.loads(text))      # [] when valid
findings = lint(json.loads(text))                      # [] when privacy-clean
dist = distribution([3.0, 1.0, 2.0], unit="s", source="assay-clock")
```

## Command line

`rigor-record` is built on `cli-extended` (a real wheel dependency; the library modules above stay stdlib-only):

```bash
rigor-record assemble --project P --tag P-v1.2.0 --commit C --project-dir P --fragments F \
  --review review.md --artifacts build.json --waivers P/rigor/waivers.toml --out OUT
rigor-record render --kind views --project-dir P        # writes P/rigor/index.json and P/RIGOR.md
rigor-record check  --project-dir P                     # offline: records valid, views equal their regeneration
rigor-record check  --project-dir P --waivers --release-version 1.2.0   # pre-gate waiver preflight
```

`assemble` refuses a fragment judged on another project tree, a review that does not bind the candidate commit, an
expired waiver and every other inconsistency, naming each cause (SPEC 13).

```bash
rigor-record sync   --project P --tag P-v1.2.0 --project-dir P --push     # commit the release asset's record to main
rigor-record update --fragments INBOX --attempts 2 --closeout-exit 1 --base P-v1.1.0 --project-dir P --push   # R2: the commit comes from the fragments
rigor-record update --tag P-v1.2.0 --reconcile --project-dir P --push     # apply waivers.toml survivor rulings
rigor-record update --tag P-v1.2.0 --fragments Q --qualification promotion --stable --project-dir P --push
rigor-record update --withdraw P-v1.1.0 --reason WHY --replacement P-v1.2.0 --project-dir P --push
rigor-record check  --project-dir P --repo . --commits origin/main~20..origin/main   # prefix + trailer check
```

`sync` and `update` run the git procedure of SPEC 16 (flock, temporary worktree at `origin/main`, compare-and-swap,
`git commit --only`, bounded push retry; without `--push` they only report). Tests run against a local bare remote,
never a real one. `queue`, `base`, `adopt` and `note` arrive with RR-4 and GN-1. Gate: `run-gate r0-r1` from the project's worktree (100 % line and branch
coverage).
