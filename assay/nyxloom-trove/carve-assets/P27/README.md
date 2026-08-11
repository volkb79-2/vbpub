# P27 carve assets — carver-owned, implementer must not edit

Frozen by C-sol-1 at main `016863a4792e2cc6c3ef1eb472cb91314f109cb2`.
Full narrative: `../../reports/assay-P27-JIT-CARVE.md`.

**P27 is NOT READY.** Work item 6's block-to-line grammar is BLOCKED on a
product decision under A-172 — read `BLOCKED-grammar.md` first. Work items 5
and 9's line-attribution oracles depend on that ruling and are therefore not
frozen. Everything else in this directory is decision-independent and stands.

| asset | status | what it fixes |
|---|---|---|
| `BLOCKED-grammar.md` | **blocking** | the three-witness contradiction, the computed evidence, and the three product options |
| `pinned-environment.json` | frozen | every image/toolchain input as a digest or repository source of truth; required gate assertions; forbidden operations |
| `fixture/shared/{go.mod,doc.go,dot-assay-gitignore}` | frozen | files identical across both commits. `dot-assay-gitignore` is the content of `repo/.assay/.gitignore` (`*` plus `!.gitignore`), stored under a non-dot name so it cannot shadow this asset tree |
| `fixture/commit1/{calc.go,calc_test.go}` | frozen | base commit — `Add` + `Classify` only |
| `fixture/commit2/{calc.go,calc_test.go}` | frozen | head commit — adds `Sum` (one statement across three physical lines) and `Unused` (wholly uncovered) |
| `witness/coverage-commit1.out` | frozen | real profile, base commit. `go` reported 75.0% (3/4) |
| `witness/coverage-commit2.out` | frozen | real profile, head commit. `go` reported 71.4% (5/7); reproduced byte-identically twice |
| `witness/shapes.go` + `coverage-shapes.out` | frozen | half-open proof (shared boundary positions) across switch, bare block, multi-line condition |
| `witness/edge.go` + `coverage-edge.out` | frozen | the end-column-1 case, and the proof that it discriminates nothing |
| `manifest/calc-statements.json` | frozen | the independent third witness, authored from source bytes before any profile existed |
| `expected/missing-tool-v4.json` | frozen | the complete hand-authored v4 `NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL` document (work item 8) |
| `probe-results.json` | frozen | all six probes, exact commands, digests, and computed outcomes |

## Reproducing the witnesses

The devcontainer has no Go toolchain and must not acquire one (A-042/A-043,
cockpit doctrine). Probes ran in `tester-unified-go:local` with `--network=none`
under the verified cgroup parent. This devcontainer's `/tmp` is not visible to
the Docker daemon at the same path, so the probes piped the tree in via `tar`
rather than a bind mount; the gate itself binds the repository by its HOST path.

```sh
tar -cf - repo | docker run -i --rm --network=none \
  --cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND" \
  -e GOPROXY=off -e GOWORK=off -e GOTOOLCHAIN=local \
  tester-unified-go:local bash -c '
    mkdir -p /tmp/w && tar -C /tmp/w -xf - && cd /tmp/w/repo/app
    go test ./... -coverpkg=./... -covermode=atomic -coverprofile=/tmp/w/c.out
    cat /tmp/w/c.out'
```

## Asset hashes

```text
a05b1f99239dedcc7af225d8416e6c934fb4fc2b2ca123922d3930d83b394633  expected/missing-tool-v4.json
6ee67d7e446064b1f71b985d5d75123a4d16becfaf2807aba3dda4be077c7176  fixture/commit1/calc.go
2e4b1544ddf2ee6d3dc3cea233ccd934403dee1350ba38c721fccdee8aa40716  fixture/commit1/calc_test.go
dcc6ac989fb6938d9d67b8cedc02b0a008b4b643eeec18be4d6a8aa653a9c6d3  fixture/commit2/calc.go
80ecff85fb03e39ff9e45cb7d0a9d86d712474477b89d37a30dc912b750a365b  fixture/commit2/calc_test.go
b56afacacdcbdc7007dde6d1b6e70cfb1a7326178aede03221a4d214fc8f1606  fixture/shared/doc.go
240a3e0d37d2e86b614063f5347eb02d4f99ca6c254de6b82871ff8d95532a7d  fixture/shared/dot-assay-gitignore
5933b6648c7f87e0774fd583014a1cc3047730c70f128d73e295429a8703af8e  fixture/shared/go.mod
3e0d1ab13c4801561e7ef04f9f1367682bff0149fcff9419ed2ac60b2de7d5b8  manifest/calc-statements.json
15efb1617fb87a110dbc62ab61df0ac7766fa3652e6db70371832285b75a146e  pinned-environment.json
eef401573f6dbb4063ee4eae985671a53edc176f2407a22a41c48fe33a18d0ab  witness/coverage-commit1.out
50ea01c3ae44afa6a7154213ea7f6ab6d60c61fdf3fc9aebbafb2d297b1619cb  witness/coverage-commit2.out
da727ce5a38dd681791cfbacc685f240ab9902312c1ad3c10c0be180dac26aaf  witness/coverage-edge.out
05529b09a04620a4798e03d081d7a890c86557a5795ce0e283d5b4842ed85331  witness/coverage-shapes.out
ecf202ddc2821047498ccda9bd97f5998bce8d1fb804e460cd3298ee9b49407e  witness/edge.go
63cd7c20566aa134f4c7b445398ef2cdcca04b6b87687255d76d1cde367669d8  witness/shapes.go
```

`BLOCKED-grammar.md`, `README.md` and `probe-results.json` are excluded from the
list above because they reference it.
