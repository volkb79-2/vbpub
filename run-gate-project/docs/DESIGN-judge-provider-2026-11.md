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
```

`exit_map` is OPTIONAL here — the judge's own refusals do not need one (see §3):
the lane still maps its own verdict codes for itself, exactly as any command
lane does.

and Run-Gate does the rest: it mounts the judge's two source trees **read-only**
at their own paths inside the judged worktree, prepends the judge's import roots
to `PYTHONPATH`, proves the lane's interpreter can only resolve the judge
Run-Gate supplied, and records the judge's identity (commit, version, dirt
state) in the run record the verdict is stored beside.

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

**What makes the overlay effective is the DESTINATION, not the argv order.**
An earlier cut of this note (and the code comment it mirrored) claimed "a later
bind mount wins for its subtree". Measured on a real daemon, that is simply
false: Docker orders bind mounts by destination DEPTH, and two mounts at ONE
destination are refused with `Duplicate mount point: <path>`. A deeper
destination inside a judge tree wins for that subtree — verified both ways:
with that path present in the judge tree the child's contents replace the
judge's, and with it absent the container fails
`mkdirat ... read-only file system`. Appending the overlay last therefore
proved nothing, and a test pinning argv ORDER would have certified a false
claim.

So Run-Gate does not rely on ordering at all. `refuse_mounts_over_the_judge`
examines every mount the lane could have contributed — `container_mount_flags`,
`RUN_GATE_EXTRA_MOUNTS`, the CoW root, the KSM preload, the observer socket —
and refuses (before `docker run`) any OTHER destination that equals a judge
destination or lies under it. A SHALLOWER destination (the judged worktree
itself, which is the judge tree's parent) is deliberately accepted: the deeper
judge mount still wins there, and a refusal that also matched it would refuse
every healthy judge lane.

The mount set is built in **one** place (`judge_mount_flags`), and the
collision rule derives its judge destinations from that function's own output,
so the refusal cannot describe a mount set the caller does not emit.

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

`python3` missing in the image is refused **inside the lane**, naming
`judge = "assay"` and the image: Run-Gate cannot prove an image's PATH from the
host, and a silent fallback interpreter would be the masked-default hazard.

Both in-lane refusals end on `JUDGE_REFUSAL_EXIT_CODE` (63), a code Run-Gate
RESERVES and maps to `ERROR` (reason `judge-not-provided`) BEFORE it consults
the lane's `exit_map`. That is the fix for a real boundary defect: on the
ordinary code 2 an unmapped refusal was reported as a FAILED LANE, and
`exit_map = { "2" = "PASS" }` made it a green one. The entity that emits a
refusal owns what it means — a lane author must not have to remember a mapping,
and must not be able to declare one that certifies a lane that never ran. A
judge lane that tries to remap 63 is refused at load, because that entry would
otherwise be legal and inert (the RG-32 shape). No `exit_map` entry is required
of a judge lane.

Immediately after the environment is set, one guard runs (the same technique
`assay_source_setup` uses for source-mode assay lanes, without importing
package code):

```
"$RUN_GATE_JUDGE_PYTHON" -c <find_spec guard> <entry> <entry> <entry>
```

It resolves `assay`, `assay_analysis` and `cowtree` with `importlib.util.find_spec`
— no package code is executed — and requires **every** origin to lie under one
of the entries Run-Gate just exported. A mismatch is a refusal on the RESERVED
code (§3) carrying the module name, the origin it actually resolved and the
fact that it is not the judge run-gate provided. Without this guard the export
is only a *request*: an image that already carries an assay would silently
judge with its own copy.

## 5. How the judge's identity is determined, and where it is recorded

Derived on the host, from the judged tree, before the lane starts:

| Field | Source | If unavailable |
|---|---|---|
| `commit` | `git rev-parse HEAD` of the judged worktree (the commit the verdict describes) | refused — a lane cannot run without a commit anyway |
| `version` | `git describe --tags --long --match 'assay-v*'` (assay's own tag grammar) | `null`, plus `version_source: "no assay-v* tag is reachable from this commit"` — *stated*, never guessed at |
| `dirty` | `git -c status.showUntrackedFiles=all status --porcelain --ignored=matching -- assay libraries/cowtree`, scoped to the judge trees | `null` + `dirty_source` = could not determine (never folded into "clean") |
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
| `judge` together with `pins` | `ERROR` at load | that pins verify an external judge artifact, so the two contradict each other |
| another mount targets a judge tree (the lane's own set or `${RUN_GATE_EXTRA_MOUNTS}`) | `ERROR` before `docker run` | the colliding destination and the judge destination it would replace |
| resolved environment not `ephemeral` (`exec`, `host`, `bare-host`, an inherited central declaration, or an undeclared name) | `ERROR` at load, decided by the same resolver a run uses | the resolver's own source-specific message: the mode and the reason (Run-Gate provides the judge by *mounting* it, and it neither starts an exec runner nor mounts anything into a host subprocess), or the migration remedy for a legacy name |
| `<w>/assay` or `<w>/libraries/cowtree` absent, or `assay/pyproject.toml` absent | `NOT_RUN 3`, reason `external-missing`, **before** admission, locks or a container; a SEQUENCE decides it before its first member runs | the missing path, the lane, and the remedy (a checkout without assay cannot provide it; declare the lane where assay lives, or use an external judge via `kind = "assay"` + `assay_command`) |
| the judged project IS a judge tree, or lives inside one | `ERROR` at load | both paths: the read-only overlay would make the judged project read-only, so a self-judging lane cannot declare this |
| no `python3` in the lane image | lane refusal on the RESERVED code, which Run-Gate maps to `ERROR` (`judge-not-provided`) before `exit_map` | `judge = "assay"` and the image |
| an `assay`/`assay_analysis`/`cowtree` that is not the provided one | the same reserved-code refusal | the module, its real origin, and the provided entries |
| a judge lane `exit_map` that remaps the reserved code | `ERROR` at load | that the code belongs to Run-Gate's own refusal, and the entry would be inert (the RG-32 shape) |

Every refusal is *before* the judge runs. None of them degrades to "run without
the judge".

## 7. Interactions

- **`network = "none"`**: unchanged and still correct. The judge is stdlib-only
  and mounted; nothing is fetched. This is the second reason (besides the
  no-pip image) the mount-and-`PYTHONPATH` shape exists instead of an
  install step — `assay_source_setup`'s editable install needs pip, this needs
  nothing but an interpreter.
- **Cow / Cowtree mounts**: the overlay is two subtrees of the judged worktree,
  dual-mounted like every other path; a CoW root mounted ELSEWHERE is
  untouched (`refuse_mounts_over_the_judge` refuses one that would land on a
  judge tree), and the judge trees stay read-only inside it. Assay's own lazy
  workspace importer finds `cowtree` on the exported `PYTHONPATH`
  (`<w>/libraries/cowtree/src`), which is the same declaration assay's own
  Python lanes make.
- **Existing assay lanes**: behaviour is byte-identical. `kind = "assay"` lanes
  never enter `judge_mount_flags`, `judge_source_setup` or the preflight
  (each returns early on the lane key), and none of their argv, state, verdict
  or identity checks changed. A project may hold both shapes side by side.
- **`--dry-run`**: the planned mount set and the judge line are disclosed; the
  preflight still refuses a missing source (a dry run of a lane that cannot be
  provided is not a plan).
- **`--worktree`**: the judge is taken from the *judged* tree (RG-15's rule),
  never from the invoking checkout.
- **A native sequence**: each judge member is preflighted at the sequence's own
  start (`sequence_judge_preflight`), so an `external-missing` precondition is
  never discovered after the members ahead of it have spent their budgets. The
  member still preflights itself when run alone; nothing else about a
  sequence changes.
- **Re-attach / collect**: the inflight record carries the identity, and
  `adopt_inflight_start` adopts it, so a surviving container's history entry
  names the judge it was started with rather than today's worktree. A record
  written before this field existed records no judge at all.
- **`hostobs` migration**: deliberately NOT part of this change. Its interim
  route stays byte-identical until the follow-up package moves it, and the two
  routes must reproduce the same totals and the same FAIL on a removed test
  before that happens. **Prerequisite, filed as RG-286:** a REAL-image smoke of
  `judge = "assay"` (real Assay source, the real `tester-unified-rust` image,
  and Docker's own `:ro` enforcement) — this change's oracles run a stand-in
  judge and execute the assembled inner script on the host, where `:ro` is an
  argv string rather than a kernel mount, so they cannot prove that.

## 8. Known limits (stated, not hidden)

- `exec`-mode and `host`/`bare-host` environments cannot be served: Run-Gate
  cannot add a mount to a runner it did not start, and a bare host subprocess
  has no mount boundary at all. Both are refused by name rather than "supported
  by luck of the PATH".
- A wheel/OCI judge artifact is out of scope: in-repo consumers follow
  `assay/docs/INTERNAL-CONSUMERS.md` (source, no pinned copy), external
  consumers keep the pinned-artifact path. Concretely: a project with no `assay/`
  of its own (a standalone consumer such as `dstdns`) is refused
  `external-missing` by `judge = "assay"` and must use `kind = "assay"` +
  `assay_command` + `pins`. A run-gate-owned judge SOURCE for external checkouts
  (a pinned wheel or pinned checkout) is a separate, unbuilt capability, filed
  as RG-287.
- The identity records the *tree-level* `dirty` state scoped to the judge
  roots. It does not hash the judge's bytes; the commit plus the read-only mount
  are the boundary (assay's own `judge_provenance` covers the artifact case).
- The read-only overlay is enforced by Docker's mount rules, not by the gate: a
  run-gate lane on a host whose daemon ignores a `:ro` bind would silently lose
  it. Nothing here checks that back, which is exactly why the real-image smoke
  (RG-286) must exist before `hostobs` migrates.
