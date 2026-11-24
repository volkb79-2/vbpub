# R2 store of record: one durable whole-source mutation store per lane (B562 part 3)

Status: design, 2026-11-24 (P-assay-07, programme 2026-11-24 decision D-A1). Owner of the semantics: assay. Owner of the transport: the controller tooling / remote-gate (run-gate RG-298, controller CTL-51). Related: `DESIGN-SUBFILE-CANDIDATE-IDENTITY.md` (§3.3.1 provenance, §3.4 resume/merge/shards).

## 1. Problem

Every R2 saving (resume, `--carry-forward`, the isolated replay of WP4) reuses earlier records, and it can only reuse what exists in ONE place. Today the records are scattered and, on remote hosts, lost: run-gate keeps its stores below the checkout it runs in (`<checkout>/.run-gate/assay-state/<project>/` for resumable runs, `<checkout>/.run-gate/assay-shards/<project>/<rg-lane>/<assay-lane>/<K>of<N>` for shards, RG-38/49/265). A remote-gate run clones a throwaway host checkout (`<host repo>/.worktrees/rg-<run-name>-<sha>/`, a standalone clone, so run-gate's repo IS that checkout) and removes it after the run (measured on v1001 2026-11-24: only the live run's checkout exists), so its store dies with it. remote-gate ships back only `verdict*.json`, `progress*.jsonl`, `coverage*.json`, `*.log` and `*.xml`, and the committed `cmru/docs/evidence/r2-*` directories hold progress streams, not state records. So no remote run can resume another, a v1001 shard store and an r1002 shard store can never meet, and no complete whole-source store exists. `run-gate doctor --prune` (RG-294/295) never removes `assay-state`/`assay-shards`; the working copies' lifetime is the transport's.

## 2. Decision

1. **One canonical store per (project, assay lane)** in the cockpit's main checkout: `<repo>/<project>/.assay/r2-store/<assay-lane>/` (gitignored by the root `.assay/` rule; never in a worktree, which is removed after merge; never under `/tmp`). Example: `/workspaces/vbpub/cmru/.assay/r2-store/cmru-first-release-r2/`.
2. **Its layout is exactly an assay mutation state directory** (flat `<candidate_id>.json` records plus `judges/<judge_sha256>/<candidate_id>.json` generations, B527) plus one `MANIFEST.json`. No third layout: run-gate's per-run stores stay the working copies, and every operation on the store is an existing assay operation (`assay run --state-dir`, `--merge-shard-store`, `--merge-state`, `--carry-forward`, `assay plan --list-pending`).
3. **One writer.** Only the cockpit promotes into the store of record (by hand now, by tooling later). Hosts never write it; they write their working copies, which are collected and merged.

## 3. The manifest (`assay-r2-store-manifest/1`)

| Field | Meaning |
|---|---|
| `schema` | `"assay-r2-store-manifest/1"` |
| `project`, `assay_lane`, `run_gate_lane` | e.g. `cmru`, `cmru-first-release-r2`, `assay-first-release-r2` |
| `source_commit` | the commit X whose whole source the store covers |
| `assay_source_commit` | the commit whose `assay/src` judged it (internal `kind = "assay"` lanes run assay from the judged worktree) |
| `judge_sha256` | the current (effective) judge of the flat records |
| `planned_total`, `record_total`, `outcomes` | `assay plan` total at X, flat records under `judge_sha256`, counts by bucket |
| `pending_total` | `assay plan --list-pending --judge <judge_sha256>` count against the store at X |
| `unresolved_total` | flat records under `judge_sha256` whose bucket is not `killed` or `survived` (crashed, hung, budget_exceeded, equivalent: never carry-importable, re-run by the next release) |
| `complete` | `pending_total == 0` and `unresolved_total == 0` and every flat record's effective judge equals `judge_sha256` |
| `generations` | the `judges/*` directory names present, each with its record count |
| `provenance_counts` | records by `provenance.kind` (absent = fresh), once B563 is merged |
| `sources` | the host runs merged in (run name, host, shard `K/N`, the staged tgz sha256) |
| `records_sha256` | sha256 over the sorted `(candidate_id, sha256(record bytes))` list of the flat records |
| `updated_at` | UTC timestamp of the promotion |

`pending_total` is always computed WITH `--judge`: without it, `--list-pending` counts a candidate as done when any record of any judge (including `judges/*` generations) is terminal. The manifest is derived data: it is recomputed from the directory on every promotion, and a mismatch between manifest and directory means "recompute", never "trust the manifest".

## 4. Invariants

- **Self-sufficiency.** A `complete` store at X is a sufficient carry source for the next release X+1: every candidate of X has a killed or survived record under one effective judge (fresh, imported, carried or replayed, each disclosed by provenance). A store with `unresolved_total > 0` is promoted but not `complete`; its unresolved candidates are re-judged by the next run and the count is disclosed in the hand-over.
- **Single judge.** The flat records of a store share one effective judge. Before scaling a sharded run across hosts, shard 1 of each host must show the same `judge_sha256` (image or env drift between v1001 and r1002 would split the judge); a mixed set cannot be merged (below).
- **Prune rule.** Only a `complete` store is pruned. After promotion, a `judges/<J>/` generation is deleted when no flat record's `provenance.from_judge_sha256` (or schema-1 `carry_over.from_judge_sha256`) names `J`. Generations serve only a resume after a judge flips back; carried records are self-contained. An incomplete store is never pruned.
- **Atomic promotion.** The new store `r2-store/<lane>.new/` starts as a hard-link copy of the current store (`cp -al r2-store/<lane> r2-store/<lane>.new`), so its `judges/` generations and every record the merge does not cover are kept; the merge runs into that copy (assay replaces a record by writing a new file, so the hard links never alter the old store). Its manifest is computed and checked, then it is renamed over the old one; the previous store is kept as `r2-store/<lane>.prev/` until the next promotion.

## 5. The cycle of one run

1. **Seed.** Seeding gives a run the prior records WITHOUT putting foreign records into a shard directory (`--merge-shard-store` refuses a shard store holding records outside its shard K/N, `_validate_shard_stores`):
   - a whole-lane resumable run: the store of record is seeded into the run's `assay-state/<project>/` working copy;
   - a shard K/N: only that shard's slice is seeded into `<K>of<N>` (the ids `assay plan --shard K/N --list-pending` prints, with their records), or nothing at all for a first backfill;
   - with `--carry-forward` (WP3a): the store of record is seeded to a separate directory and passed as the carry source, never into the working copy.
2. **Run.** The lane runs as today (`remote-gate.sh <host> <project> <lane> <sha> [-- --shard K/N]`).
3. **Collect.** Inside the run, before the throwaway checkout is removed, the working copy is packed (`--collect-store`, §6) and staged in the cockpit at `<repo>/<project>/.assay/r2-staging/<run-name>/` with its sha256.
4. **Merge and check.** The merge is an `assay run` of the lane at X with `--state-dir r2-store/<lane>.new` (prepared as in §4), choosing the strict or the lenient form:
   - a full N-shard campaign: `--merge-shard-store` with ALL N staged shard stores. It is the strict whole-lane merge: exactly N stores, each with a `shard.manifest` matching the current plan and judge, complete, with no foreign records; anything else is refused;
   - anything else (a non-shard `assay-state` copy, a partial set of shards, a store from a run that ended early): `--merge-state`, which takes only same-judge records and re-executes the rest instead of refusing.
   Then `assay plan --list-pending --judge <judge_sha256>` against the result and the manifest (§3).
5. **Promote** (atomic rename, §4) and prune if `complete`; report the manifest numbers in the hand-over.

## 6. Interface for the transport (RG-298 / CTL-51)

Wanted from remote-gate (semantics stay here; the transport only moves bytes):
- `--seed-store <local dir>=<host path>`: before the run, upload the directory (tar + sha256) and unpack it at that host path, replacing what is there.
- `--collect-store <host path>`: after the lane, inside the run, pack that directory into `assay-store.tgz` + `assay-store.tgz.sha256` in the run's artifacts.
- Agreed with the transport owner (2026-11-24): (1) the host path is relative to the run's OWN host checkout, normalised; absolute paths, `..` and symlinked components are refused (exit 2) before any upload, so concurrent runs never touch each other's store; (2) both flags are repeatable, the local seed side must be an existing directory; (3) the seed is unpacked after the checkout is prepared and before the lane starts, and a seed failure aborts the run as a transport error, never a lane FAIL; (4) a store absent after the run yields `STORE-ABSENT <path>` and no tgz, a collect failure `STORE-COLLECT-FAILED <path> <reason>`, neither changes the lane's verdict or exit code; (5) the size bound counts uncompressed tar bytes in both directions (`--store-max-mib`, default 512); over it the seed refuses and the collect skips with `STORE-TOO-LARGE`.
- Never `/tmp`, exact paths only. The merge and promotion stay in the cockpit (§5 steps 4-5).
- Collection must happen BEFORE the throwaway checkout is removed, so it is part of the remote run itself. The cmru backfill (B562 part 2) therefore cannot start until `--collect-store` exists: without it every shard's records are lost.

## 7. Interim manual recipe (until §6 ships)

Only for a store whose directory still exists and is no longer being written (a capped one-off container run with an explicit persistent `--state-dir` outside any throwaway checkout, after the run ended): `hx.sh <host> 'tar -C <state dir parent> -czf - <state dir name>' > <repo>/<project>/.assay/r2-staging/<run-name>/<name>.tgz`, record its sha256, unpack beside it, then §5 steps 4-5 with `--merge-state` (a hand-copied store has no complete shard set). Never tar a store while a run writes it. Seeding is the reverse direction with the same exact paths. Never copy through `/tmp`. A finished remote-gate run's store cannot be recovered.

## 8. Known limits (by design)

- One writer by procedure, no lock file: two cockpit sessions promoting the same lane at once is out of scope.
- The store lives in one cockpit checkout. Its backups are `r2-store/<lane>.prev/` and the staged `r2-staging/<run-name>/*.tgz` files (the host working copies die with their run); a cockpit loss beyond those loses the store.
- The store is per assay lane: a lane rename starts a new store.
