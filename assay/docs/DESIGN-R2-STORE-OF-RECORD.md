# R2 store of record: one durable whole-source mutation store per lane (B562 part 3)

Status: design, 2026-11-24 (P-assay-07, programme 2026-11-24 decision D-A1). Owner of the semantics: assay. Owner of the transport: the controller tooling / remote-gate (run-gate RG-298, controller CTL-51). Related: `DESIGN-SUBFILE-CANDIDATE-IDENTITY.md` (§3.3.1 provenance, §3.4 resume/merge/shards).

## 1. Problem

Every R2 saving (resume, `--carry-forward`, the isolated replay of WP4) reuses earlier records, and it can only reuse what exists in ONE place. Today the records are scattered and, on remote hosts, lost: run-gate keeps its stores below the checkout it runs in (`<checkout>/.run-gate/assay-state/<project>/` for resumable runs, `<checkout>/.run-gate/assay-shards/<project>/<rg-lane>/<assay-lane>/<K>of<N>` for shards, RG-38/49/265). A remote-gate run uses a throwaway host checkout (`<host repo>/.worktrees/rg-<run-name>-<sha>/`) that is removed after the run (measured on v1001 2026-11-24: only the live run's checkout exists), so its store dies with it. remote-gate ships back only `verdict*.json`, `progress*.jsonl`, `coverage*.json`, `*.log` and `*.xml`, and the committed `cmru/docs/evidence/r2-*` directories hold progress streams, not state records. So no remote run can resume another, a v1001 shard store and an r1002 shard store can never meet, and no complete whole-source store exists. `run-gate doctor --prune` (RG-294/295) never removes `assay-state`/`assay-shards`; the working copies' lifetime is the transport's.

## 2. Decision

1. **One canonical store per (project, assay lane)** in the cockpit's main checkout: `<repo>/<project>/.assay/r2-store/<assay-lane>/` (gitignored by the root `.assay/` rule; never in a worktree, which is removed after merge; never under `/tmp`). Example: `/workspaces/vbpub/cmru/.assay/r2-store/cmru-first-release-r2/`.
2. **Its layout is exactly an assay mutation state directory** (flat `<candidate_id>.json` records plus `judges/<judge_sha256>/<candidate_id>.json` generations, B527) plus one `MANIFEST.json`. No third layout: the per-host stores of run-gate stay the working copies, and every operation on the store is an existing assay operation (`--resume`, `--merge-shard-store`, `--carry-forward`, `assay plan --list-pending`).
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
| `pending_total` | `assay plan --list-pending` count against the store at X |
| `complete` | `pending_total == 0` and every flat record's effective judge equals `judge_sha256` |
| `generations` | the `judges/*` directory names present, each with its record count |
| `provenance_counts` | records by `provenance.kind` (absent = fresh), once B563 is merged |
| `sources` | the host runs merged in (run name, host, shard `K/N`) |
| `records_sha256` | sha256 over the sorted `(candidate_id, sha256(record bytes))` list of the flat records |
| `updated_at` | UTC timestamp of the promotion |

The manifest is derived data: it is recomputed from the directory on every promotion, and a mismatch between manifest and directory means "recompute", never "trust the manifest".

## 4. Invariants

- **Self-sufficiency.** A `complete` store at X is a sufficient carry source for the next release X+1: every candidate of X has a record under one effective judge (fresh, imported, carried or replayed, each disclosed by provenance). Nothing outside the store is needed to plan, resume or carry forward.
- **Single judge.** The flat records of a store share one effective judge. A merge of host stores with different judges is refused (the existing `--merge-shard-store` rule). Before scaling a sharded run across hosts, shard 1 of each host must show the same `judge_sha256` (image or env drift between v1001 and r1002 would split the judge).
- **Prune rule.** Only a `complete` store is pruned. After promotion, a `judges/<J>/` generation is deleted when no flat record's `provenance.from_judge_sha256` (or schema-1 `carry_over.from_judge_sha256`) names `J`. An incomplete store is never pruned.
- **Atomic promotion.** The new store is assembled beside the old one (`r2-store/<lane>.new/`), its manifest computed and checked, then renamed over the old one; the previous store is kept as `r2-store/<lane>.prev/` until the next promotion.

## 5. The cycle of one run

1. **Seed.** Before the lane runs on host H, the store of record is copied into H's working copy (`assay-state/<project>/` for a resumable run, or each shard's `<K>of<N>` directory; until `--carry-forward` exists it serves resume only). With `--carry-forward`, the seeded copy is passed as the carry source.
2. **Run.** The lane runs as today (`remote-gate.sh <host> <project> <lane> <sha> [-- --shard K/N]`).
3. **Collect.** The host working copy is packed (`tar` of the record directory, with a sha256) and copied to the cockpit staging area `<repo>/<project>/.assay/r2-staging/<run-name>/`.
4. **Merge and check.** `assay … --merge-shard-store` (same effective judge) into `r2-store/<lane>.new/`; `assay plan --list-pending` against it; manifest computed.
5. **Promote** (atomic rename) and prune if `complete`; report the manifest numbers in the hand-over.

## 6. Interface for the transport (RG-298 / CTL-51)

Wanted from remote-gate (semantics stay here; the transport only moves bytes):
- `--seed-store <local dir>=<host path relative to the host repo>`: before the run, upload the directory (tar + sha256) and unpack it at that host path, replacing what is there.
- `--collect-store <host path relative to the host repo>`: after the run, pack that directory into `assay-store.tgz` + `assay-store.tgz.sha256` in the run's artifacts.
- Bounded size (refuse above a configured limit, default 512 MiB), never `/tmp`, exact paths only, the lane's own verdict untouched. The merge and promotion stay in the cockpit (§5 steps 4-5).
- Agreed with the transport owner (2026-11-24): (1) the host path is relative to the run's OWN host checkout, normalised; absolute paths, `..` and symlinked components are refused (exit 2) before any upload, so concurrent runs never touch each other's store; (2) both flags are repeatable, the local seed side must be an existing directory; (3) the seed is unpacked after the checkout is prepared and before the lane starts, and a seed failure aborts the run as a transport error, never a lane FAIL; (4) a store absent after the run yields `STORE-ABSENT <path>` and no tgz, a collect failure `STORE-COLLECT-FAILED <path> <reason>`, neither changes the lane's verdict or exit code; (5) the size bound counts uncompressed tar bytes in both directions (`--store-max-mib`, default 512); over it the seed refuses and the collect skips with `STORE-TOO-LARGE`.
- Collection must happen BEFORE the throwaway checkout is removed, so it is part of the remote run itself. The cmru backfill (B562 part 2) therefore cannot start until `--collect-store` exists: without it every shard's records are lost.

## 7. Interim manual recipe (until §6 ships)

Only for a run whose checkout still exists (a lane still running, or a capped one-off container run with an explicit persistent state directory outside any throwaway checkout): `hx.sh <host> 'tar -C <checkout>/.run-gate/assay-shards/cmru/assay-first-release-r2/cmru-first-release-r2 -czf - <K>of<N>' > <repo>/cmru/.assay/r2-staging/<run-name>/<K>of<N>.tgz`, record its sha256, unpack beside it, then §5 steps 4-5. Seeding is the reverse direction with the same exact paths. Never copy through `/tmp`. A finished remote-gate run's store cannot be recovered.

## 8. Known limits (by design)

- One writer by procedure, no lock file: two cockpit sessions promoting the same lane at once is out of scope.
- The store lives in one cockpit checkout; a cockpit loss loses it. The host working copies are the backup until the next promotion.
- The store is per assay lane: a lane rename starts a new store.
