# W3 carve assets — the A-279 ordering pair

**Captured 2026-08-18 by the controller, driving the shipped `assay run` CLI**
at branch `assay-P34-sql-adapter` after W5+W6 landed (`2c1a57cc`). These are
evidence. **Never edit them.** If assay's behaviour changes, capture a NEW
asset and record what it proves; do not bring these to green.

## What they prove

A-279 was found by reading the carve's §3.4 against its §3.6, and confirmed by
the adversarial review reading `safeio.arm()`'s unlink semantics. It was, until
these two documents, a *reasoned* finding. It is now a **measured** one.

Two repositories, identical in every respect — same DDL, same lane, same
`sql:drop-not-null` mutant on the same line — differing **only** in the
ordering of the project-declared consumer command:

| asset | consumer command | `killed` | `crashed` | lane outcome |
|---|---|---|---|---|
| `a279-correct-ordering-killed.json` | `apply && dump && test` | **1** | 0 | `PASS`, exit 0 |
| `a279-carve-ordering-crashed.json` | `apply && test && dump` | **0** | 1 | `ERROR`/`EXEC_FAILED`, exit 2 |

```
sha256  65ae78d8faf3850be1ec904bfda8b104259cfadb59c08d8f5922b0a040f546ce  a279-correct-ordering-killed.json
sha256  80f55361982675c24b0305454086c930e81564adb73f1e1e78b0b34395af094e  a279-carve-ordering-crashed.json
```

## Why the difference exists

A kill **is** the test exiting non-zero. Under `apply && test && dump` the `&&`
short-circuits, so the dump the classification depends on never runs;
`safeio.arm()` has already unlinked any pre-existing file, so the artifact is
absent; §3.6 reads absent-on-`FAIL` as `crashed`; and `judge_mutation` ranks
`crashed` above every other bucket, so one such mutant renders the whole lane
`ERROR`/`EXEC_FAILED`.

**The carve's own canonical consumer command could not produce a single kill** —
the feature's headline outcome — and none of its acceptance oracles would have
noticed, because not one of them ever produced a kill. That is why A-279 adds an
end-to-end kill oracle and not merely a reordered example.

## What they do NOT prove

These lanes use a shell script standing in for a schema gate: `apply` copies a
file, `dump` greps constraint tokens, `test` counts them. **No PostgreSQL is
involved**, so they say nothing about whether a generated mutant is valid DDL or
whether an operator's name matches a real catalog change. That is W9's job, and
the carve's §9 M5/M6/M15 hold the current (non-re-runnable) evidence for it.

They also do not prove isolation: each is a single-mutant run.

## Reproducing

The two repositories are constructed in full by the commands recorded in this
wave's session log; each is a `git init`, two commits, and the lane in §3.4's
shape with `operators = ["sql:drop-not-null"]` and
`equivalence_artifact = ".assay/schema-dump.sql"`. The only textual difference
between them is the order of the last two lines of `gate.sh`.

One trap worth recording, met while capturing these: writing the
`--verdict-json` output **into the repository under test** makes the tree dirty
and the run returns `NO_MEASUREMENT`/`DIRTY_TREE` before any mutant is
attempted. That is the mechanism working. Write the verdict outside the tree.
