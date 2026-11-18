# R-3a evidence (cargo-mutants 27.1.0, `tester-unified-rust:local`, v1001)

Every script ran inside a detached container (`--network none --memory 2g-3g
--cpus 2`) with the subject tree mounted at `/work` and results at `/out`.
Scripts write their log to `/out`; the files here were renamed when copied.

| Script (`scripts/`) | Subject | Log / results here |
|---|---|---|
| `run-spikes-s3-s4.sh` | `subject/`, `subjects/subject-v2`, `subjects/subject-cm`, `subjects/subject-bf`, `subject-diffs/` | `spikes-s3-s4.log` (written as `spikes.log`), `s3-list.json`, `full/`, `indiff/`, `basefail/` |
| `run-s3-extra.sh` | `s2/subject` (v1 tree) and `subjects/subject-s2-v2` (the tree `s2-multi-indiff.json` came from), `subject-diffs/multi.diff` | `spikes-s3-extra.log` (written as `spikes2.log`), `s2/` |
| `run-s3-reuse.sh` | `subjects/subject-v2` | `spikes-s3-reuse.log` (written as `spikes3.log`) |
| `run-workspace-1.sh`, `run-workspace-2.sh` | `workspace/ws` (crates `a`, `b`) | `workspace/out/spikes4.log`, `spikes4b.log`, `wsb-*` |
| `run-respike.sh`, `run-respike-git.sh`, `run-respike-empty.sh` | all of the above plus a git repo built in the container | `respike/` (`spikes5.log` step 2 prints `exit=?`, a script slip; the empty-diff exit status is `spikes6.log`) |

Pruned from the raw `mutants.out` trees: `debug.log`, `lock.json` (host name and
container id), most per-mutant `log/` and `diff/` files, and the caught/missed
text lists of the derived runs. Every claim in `../../RUST-R3A-SPIKES.md` names a
retained file, except that the emptiness of the rotated `mutants.out.old` in
`respike/o-pre` is inferred (that directory was pruned). `run-s3-reuse.sh`'s
last step was run with an unset shell variable in the original; the copy here
spells the command out and was not re-run. The `--package a` workspace reports (`ws-false`,
`ws-true`) were not kept; only their log is.
