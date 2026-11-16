# Design: Run-Gate provides the judge to a language lane (2026-11, RG-285)

**Status: DESIGN + shipped in the same change** (2026-11-16). Operator
decision 2026-11-16 ("I recommend run-gate providing the judge.", agreed).

Why this document exists: `hostobs`' `assay-r1` is today a `kind = "command"`
lane that sets `PYTHONPATH` to the checked-out assay source itself
(`hostobs/run-gate.toml`) because `tester-unified-rust` deliberately ships no
pip and no assay wheel. That interim route works and is proven (a PASS and a
negative probe FAIL), but every project that gains a language lane would have
to re-derive the same shell incantation, and the lane's own script — not the
gate — would decide WHICH assay judged it. This document records the
first-class form, its refusals and the boundaries it deliberately keeps.

Related documents, one job each:

- [`../README.md`](../README.md#run-gate-provides-the-assay-judge-rg-285) — what a lane can now declare.
- [`../CONSUMERS.md`](../CONSUMERS.md#run-gate-provided-assay-judge-rg-285) — how to adopt it, pasted examples.
- [`../SPEC.md`](../SPEC.md) — the normative contract (`R-08f`, `R-57`).
- this file — why, and what was rejected.

## 1. The decision

> The judge belongs with the thing that judges. Run-Gate already owns mounts,
> cgroups, images and verdict records; assay already owns *how* to judge. A
> language image owns neither.

So a lane declares, in one line, that it wants the judge:

```toml
[lanes.assay-r1]
kind = "command"
environment = "tester-unified-rust"
judge = "assay"
argv = ["bash", "-c", '''set -eu
"$RUN_GATE_JUDGE_PYTHON" -m assay.cli run assay-r1 --file assay.toml \
  --verdict-json .assay/verdict-assay-r1.json || rc=$?
...
''']
exit_map = { "0" = "PASS", "2" = "ERROR" }
```

and Run-Gate does the rest: it mounts the judge's source tree **read-only** on
top of the judged worktree, prepends the judge's import roots to `PYTHONPATH`,
proves the lane's interpreter can only resolve the judge Run-Gate supplied, and
records the judge's identity (commit, version, dirt state) in the run record
the verdict is stored beside.

## 2. Declaration syntax — one string key, closed vocabulary

`judge = "assay"` (lane key, `kind = "command"` only). The vocabulary is
closed: exactly `"assay"` today, and an unknown value is refused at load with
the value and the allowed set named.

Rejected alternatives, and why:

| Alternative | Why rejected |
|---|---|
| a table `judge = { name = "assay", source = "..." }` | `source` is DERIVED, not declared: the in-repo boundary is *the judged worktree's own* `assay/` (assay's own `docs/INTERNAL-CONSUMERS.md` rule for same-repository consumers). A declared path would be a second spelling of an owned fact, and a `wheel`/OCI option has no consumer yet — the external-consumer boundary is already `kind = "assay"` + `assay_command` + `pins`. |
| `judge = true` / `assay_judge = true` | a bare flag cannot say which judge, and two spellings of one capability drift. |
| deriving the feature from argv (a lane whose command mentions `assay.cli`) | argv is shell text; parsing it is how `doctor` already *advises* (RG-34) and never decides. A declaration the loader can validate beats a heuristic. |
| baking assay into `tester-unified-rust` (or any image) | couples image rebuilds to assay releases and repeats per-image work; the image's job is the toolchain, the gate's job is the judge. |

`judge` on `kind = "assay"` is refused by name: that lane kind already owns its
judge shape (`assay_command` / the source install), and accepting it there would
be the RG-32 "two levels, same-looking key" trap one lane kind over. Sequences
accept no runner keys at all, so a member declares it.

## 3. What is mounted, where, and why read-only

The judge source is `<judged worktree>/assay` plus the same-repository sibling
package assay imports lazily (`<judged worktree>/libraries/cowtree`). Both are
already *inside* the judged worktree's mount — writable. Run-Gate therefore adds
a **read-only overlay of each judge tree at its own path, dual-mounted** (the
physical host path and the devcontainer namespace path, exactly like every other
mount; `_dual()` is reused, never re-derived):

```
-v <phys>/assay:/assay:ro                     (both namespace destinations)
-v <phys>/libraries/cowtree:/libraries/cowtree:ro
```

Later bind mounts overlay earlier ones for the same subtree, so this lands on
top of the worktree mount and the judged process (including any test it runs)
can no longer modify the judge that produces its verdict. That is the whole
point of mounting at all: without it the overlay would be decorative.

The mount set is built in **one** place (`judge_mount_flags`) and appended *after*
every other mount (`container_mount_flags`, `RUN_GATE_EXTRA_MOUNTS`, the CoW
root, the KSM preload, the observer socket), so no operator override or CoW
layout can re-open the judge tree for writing.

No wheel, no pip, no install: assay is stdlib-only (A-005), the sources are
mounted, and the lane's interpreter finds them through `PYTHONPATH`.

## 4. Environment the judge needs

Exported by the lane's own `bash -c` (so it works for every image, and needs no
`docker run -e` shape):

| Name | Value | Why |
|---|---|---|
| `PYTHONPATH` | `<w>/assay/src:<w>/assay/analysis/src:<w>/libraries/cowtree/src` **prepended** to whatever the lane already had | the judge must win over anything the lane declares; the lane's own paths keep working after it |
| `PYTHONDONTWRITEBYTECODE` | `1` | the judge tree is read-only; never write `__pycache__` into it |
| `PYTHONNOUSERSITE` | `1` | a user-site `assay` from the image must not win over the provided one |
| `RUN_GATE_JUDGE_PYTHON` | absolute path of `python3` resolved in-lane | the lane names the interpreter once instead of guessing, and the guard below uses the very same path |

`python3` missing in the image is refused **inside the lane**, exit 2, naming
`judge = "assay"` and the image: Run-Gate cannot prove an image's PATH from the
host, and a silent fallback interpreter would be the masked-default hazard. A
judge-provided lane should therefore declare `exit_map` mapping 2 to `ERROR`
(the `hostobs` interim lane already does).

Immediately after the environment is set, one guard runs (the same technique
`assay_source_setup` uses for source-mode assay lanes, without importing
package code):

```
"$RUN_GATE_JUDGE_PYTHON" -c <find_spec guard> <entry> <entry> <entry>
```

It resolves `assay`, `assay_analysis` and `cowtree` with `importlib.util.find_spec`
— no package code is executed — and requires **every** origin to lie under one
of the entries Run-Gate just exported. A mismatch is exit 2 with the module
name, the origin it actually resolved and the fact that it is not the judge
run-gate provided. Without this guard the export is only a *request*: an image
that already carries an assay would silently judge with its own copy.

## 5. How the judge's identity is determined, and where it is recorded

Derived on the host, from the judged tree, before the lane starts:

| Field | Source | If unavailable |
|---|---|---|
| `commit` | `git rev-parse HEAD` of the judged worktree (the commit the verdict describes) | refused — a lane cannot run without a commit anyway |
| `version` | `git describe --tags --long --match 'assay-v*'` (assay's own tag grammar) | `null`, plus `version_source: "no assay-v* tag is reachable from this commit"` — *stated*, never guessed at |
| `dirty` | `git status --porcelain -- assay libraries/cowtree`, scoped to the judge trees | `null` = could not determine (it is never folded into "clean") |
| `source`, `pythonpath` | the mounted roots, verbatim | — |
| `provided_by` | `"run-gate"` | — |

Recorded in the run record under `judge`, therefore in
`<project>/.run-gate/history.json`'s `latest` entry beside the outcome, the
commit and the duration — the auditable place for "which judge produced this
verdict, on which commit, from clean code or not". It is disclosed on stdout
before the lane runs (`run-gate: judge: assay <version> at <commit> (clean;
provided by run-gate, mounted read-only from <path>)`), so it is visible in the
lane log too. It rides in the inflight record, so a re-attached or collected run
reports the judge it was *started* with rather than today's config.

`kind = "assay"` lanes are untouched: they keep their own `judge_provenance`
(`assay_version` + `commit` verified by `assay_lane_result`). This feature adds
no second parser of assay's verdict, and the lane's exit status remains the
lane's verdict (`exit_map`), exactly as for any command lane.

## 6. Refusals — never a silent skip

| Condition | Result | Message names |
|---|---|---|
| unknown `judge` value | `ERROR` at load | the value, the lane, the closed vocabulary |
| `judge` on an assay/sequence lane | `ERROR` at load | where the setting belongs instead |
| resolved environment not `ephemeral` (`exec` or `host`) | `ERROR` at load | the mode and the reason: Run-Gate provides the judge by *mounting* it, and it neither starts an exec runner nor mounts anything into a host subprocess |
| `<w>/assay` or `<w>/libraries/cowtree` absent, or `assay/pyproject.toml` absent | `NOT_RUN 3`, reason `external-missing`, **before** admission, locks or a container | the missing path, the lane, and the remedy (a checkout without assay cannot provide it; declare the lane where assay lives, or use an external judge via `kind = "assay"` + `assay_command`) |
| the judged project IS the judge tree (`project_dir` inside `<w>/assay` or `<w>/libraries/cowtree`) | `ERROR` at load | both paths: the read-only overlay would make the judged project read-only, so a self-judging lane cannot declare this |
| no `python3` in the lane image | lane exit 2 (`ERROR` through `exit_map`) | `judge = "assay"` and the image |
| an `assay`/`assay_analysis`/`cowtree` that is not the provided one | lane exit 2 | the module, its real origin, and the provided entries |

Every refusal is *before* the judge runs. None of them degrades to "run without
the judge".

## 7. Interactions

- **`network = "none"`**: unchanged and still correct. The judge is stdlib-only
  and mounted; nothing is fetched. This is the second reason (besides the
  no-pip image) the mount-and-`PYTHONPATH` shape exists instead of an
  install step — `assay_source_setup`'s editable install needs pip, this needs
  nothing but an interpreter.
- **Cow / Cowtree mounts**: the overlay is two subtrees of the judged worktree,
  dual-mounted like every other path, and appended after `cowtree_mount_flags`
  — a CoW root mounted elsewhere is untouched, and the judge trees stay
  read-only inside it. Assay's own lazy workspace importer finds `cowtree` on
  the exported `PYTHONPATH` (`<w>/libraries/cowtree/src`), which is the same
  declaration assay's own Python lanes make.
- **Existing assay lanes**: behaviour is byte-identical. `kind = "assay"` lanes
  never enter `judge_mount_flags`, `judge_source_setup` or the preflight
  (each returns early on the lane key), and none of their argv, state, verdict
  or identity checks changed. A project may hold both shapes side by side.
- **`--dry-run`**: the planned mount set and the judge line are disclosed; the
  preflight still refuses a missing source (a dry run of a lane that cannot be
  provided is not a plan).
- **`--worktree`**: the judge is taken from the *judged* tree (RG-15's rule),
  never from the invoking checkout.
- **`hostobs` migration**: deliberately NOT part of this change. Its interim
  route stays byte-identical until the follow-up package moves it, and the two
  routes must reproduce the same totals and the same FAIL on a removed test
  before that happens.

## 8. Known limits (stated, not hidden)

- `exec`-mode and `host`/`bare-host` environments cannot be served: Run-Gate
  cannot add a mount to a runner it did not start, and a bare host subprocess
  has no mount boundary at all. Both are refused by name rather than "supported
  by luck of the PATH".
- A wheel/OCI judge artifact is out of scope: in-repo consumers follow
  `assay/docs/INTERNAL-CONSUMERS.md` (source, no pinned copy), external
  consumers keep the pinned-artifact path.
- The identity records the *tree-level* `dirty` state scoped to the judge roots.
  It does not hash the judge's bytes; the commit plus the read-only mount are
  the boundary (assay's own `judge_provenance` covers the artifact case).
