# P27 carve assets — carver-owned, implementer must not edit

Frozen by C-sol-1 at main `016863a4792e2cc6c3ef1eb472cb91314f109cb2`; corrected
and extended 2026-08-11 after the CR-opus-0 carve review
(`.worktrees/_control/assay-P20-P32/carve-review-P27.md`, PARTIALLY CONFIRMED).
Full narrative: `../../reports/assay-P27-JIT-CARVE.md`.

**A-O19 is ruled: option 2** — a real source-side statement-position oracle
(decision A-217; A-172's disproved position premise closed as A-218). Read
`BLOCKED-grammar.md` first: it holds the impossibility proof and the two defects
the ruling does *not* fix.

**P27 is still NOT dispatchable.** It must be re-carved around option 2. The
assets below are the evidence and the decision-independent material; the expected
R1 `Coverage` line sets and the work item 5/6/9 oracles do not exist and must not
be invented.

| asset | status | what it fixes |
|---|---|---|
| `BLOCKED-grammar.md` | evidence + ruling | the impossibility proof, the corrected over-approximation relation, A-172's closure, and the two outstanding handoff defects |
| `pinned-environment.json` | frozen | every image/toolchain input as a digest or repository source of truth; required gate assertions; forbidden operations |
| `fixture/shared/{go.mod,doc.go,dot-assay-gitignore}` | frozen | files identical across both commits. `dot-assay-gitignore` is the content of `repo/.assay/.gitignore` (`*` plus `!.gitignore`), stored under a non-dot name so it cannot shadow this asset tree |
| `fixture/commit1/{calc.go,calc_test.go}` | frozen | base commit — `Add` + `Classify` only |
| `fixture/commit2/{calc.go,calc_test.go}` | frozen | head commit — adds `Sum` (one statement across three physical lines) and `Unused` (wholly uncovered) |
| `witness/coverage-commit1.out` | frozen | real profile, base commit. `go` reported 75.0% (3/4) |
| `witness/coverage-commit2.out` | frozen | real profile, head commit. `go` reported 71.4% (5/7); reproduced byte-identically twice, and a third time by the carve reviewer |
| `witness/collision-col{A,B}.go` + `coverage-collision.out` | frozen — **the proof** | two gofmt-clean files, byte-identical profile, statement truth `{4,6}` vs `{4,5}`. Identical input, different correct answers, so no profile-only rule can be right |
| `witness/seg.go` + `coverage-seg.out` | frozen — **discriminator** | a multi-line block starting *at* a statement. Kills rules fitted to the original four witnesses; any new oracle must be checked here |
| `witness/lit.go` + `coverage-lit.out` | frozen — **caveat** | executed-wins laundering: an uncovered func-literal body promoted by its covered enclosing block, so the over-approximation relation fails for the *missing* set |
| `witness/shapes.go` + `coverage-shapes.out` | frozen | half-open proof (shared boundary positions) across switch, bare block, multi-line condition |
| `witness/edge.go` + `coverage-edge.out` | frozen | the end-column-1 case, and the proof that it discriminates nothing (A-218) |
| `manifest/calc-statements.json` | frozen | the independent third witness, authored from source bytes before any profile existed |
| `expected/missing-tool-v4-template.json` | frozen **template** | the hand-authored v4 `NO_MEASUREMENT`/`MISSING_EXTERNAL_TOOL` document (work item 8). **Contains placeholders and is not directly valid** — see below |
| `probe-results.json` | frozen | all nine probes, exact commands, digests, computed outcomes, and the 2026-08-11 corrections |

## The missing-tool template requires substitution before use

`expected/missing-tool-v4-template.json` carries four placeholders, exactly the
convention every `expected/*-template.json` in `carve-assets/P25` and
`carve-assets/P26` uses:

| placeholder | substitute |
|---|---|
| `@ASSAY_VERSION@` | the installed wheel's version |
| `@HEAD_OID@` | the resolved full HEAD OID |
| `@STARTED@` | an ISO-8601 timestamp with an explicit offset |
| `@ENDED@` | an ISO-8601 timestamp with an explicit offset |

**As raw bytes it fails validation**, and that is expected of a template: 2
`jsonschema` errors and 3 `verify_text` failures, all four on the two timestamp
fields. *After* substitution it is schema-valid and `verify_text` returns `[]`,
with `judgment` absent as A-136 requires for a claim carrying no coverage
payload. Work item 8's instruction to install "the exact JIT-locked complete
missing-tool artifact" means the **substituted** document; installing these
literal bytes into the ordinary fixtures and removing the conformance exclusion
would redden the gate for a reason unrelated to Go coverage.

The first version of this file was named `missing-tool-v4.json` with no template
marker, and both this README and `probe-results.json` claimed it was already
valid and complete. That was an A-067-class defect — a recorded check whose
stated subject was not what was checked — found by the carve review.

## Reproducing the witnesses

The devcontainer has no Go toolchain and must not acquire one (A-042/A-043,
cockpit doctrine). Every witness came from `tester-unified-go:local` with
`--network=none` under the verified cgroup parent. This devcontainer's `/tmp` is
not visible to the Docker daemon at the same path, so the probes piped the tree
in via `tar` rather than a bind mount; the gate itself binds the repository by
its HOST path.

```sh
tar -cf - repo | docker run -i --rm --network=none \
  --cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND" \
  -e GOPROXY=off -e GOWORK=off -e GOTOOLCHAIN=local \
  tester-unified-go:local bash -c '
    mkdir -p /tmp/w && tar -C /tmp/w -xf - && cd /tmp/w/repo/app
    go test ./... -coverpkg=./... -covermode=atomic -coverprofile=/tmp/w/c.out
    cat /tmp/w/c.out'
```

The collision pair must be built as two separate modules both declaring
`module example.invalid/coll` with the file named `f.go`, or the profiles will
differ in their path prefix and the collision will be obscured.

## Asset hashes

```text
a05b1f99239dedcc7af225d8416e6c934fb4fc2b2ca123922d3930d83b394633  expected/missing-tool-v4-template.json
6ee67d7e446064b1f71b985d5d75123a4d16becfaf2807aba3dda4be077c7176  fixture/commit1/calc.go
2e4b1544ddf2ee6d3dc3cea233ccd934403dee1350ba38c721fccdee8aa40716  fixture/commit1/calc_test.go
dcc6ac989fb6938d9d67b8cedc02b0a008b4b643eeec18be4d6a8aa653a9c6d3  fixture/commit2/calc.go
80ecff85fb03e39ff9e45cb7d0a9d86d712474477b89d37a30dc912b750a365b  fixture/commit2/calc_test.go
b56afacacdcbdc7007dde6d1b6e70cfb1a7326178aede03221a4d214fc8f1606  fixture/shared/doc.go
240a3e0d37d2e86b614063f5347eb02d4f99ca6c254de6b82871ff8d95532a7d  fixture/shared/dot-assay-gitignore
5933b6648c7f87e0774fd583014a1cc3047730c70f128d73e295429a8703af8e  fixture/shared/go.mod
3e0d1ab13c4801561e7ef04f9f1367682bff0149fcff9419ed2ac60b2de7d5b8  manifest/calc-statements.json
15efb1617fb87a110dbc62ab61df0ac7766fa3652e6db70371832285b75a146e  pinned-environment.json
418d131c00e61776f1405d8bed36a657c122af9e171eb2f161de05b75a49a104  witness/collision-colA.go
03a174024e1062853405e0f61bea9f28374aa58907ee0a432f5f8707dda601be  witness/collision-colB.go
f6c593a961f742b3c809d213f290b499bfebec677d8853197e5cc6d44df14da8  witness/coverage-collision.out
eef401573f6dbb4063ee4eae985671a53edc176f2407a22a41c48fe33a18d0ab  witness/coverage-commit1.out
50ea01c3ae44afa6a7154213ea7f6ab6d60c61fdf3fc9aebbafb2d297b1619cb  witness/coverage-commit2.out
da727ce5a38dd681791cfbacc685f240ab9902312c1ad3c10c0be180dac26aaf  witness/coverage-edge.out
29b5461276a811e947773775d916dcc745c0aec70f6d136ab1814fbca602e1a8  witness/coverage-lit.out
24c212496fa481de3d60ae0ac0b31fc5cbbfa97517a56454ed77c4030f05c606  witness/coverage-seg.out
05529b09a04620a4798e03d081d7a890c86557a5795ce0e283d5b4842ed85331  witness/coverage-shapes.out
ecf202ddc2821047498ccda9bd97f5998bce8d1fb804e460cd3298ee9b49407e  witness/edge.go
e18ea21777877916b6f895e37114bea2f47f0fd661a6f94667331dee86652ba9  witness/lit.go
d5823b339b31fd330964baf6880015fc9d5d2969d0c1347367a6c211814430c5  witness/seg.go
63cd7c20566aa134f4c7b445398ef2cdcca04b6b87687255d76d1cde367669d8  witness/shapes.go
```

`BLOCKED-grammar.md`, `README.md` and `probe-results.json` are excluded from the
list above because they reference it.
