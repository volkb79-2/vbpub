# B004 carve — provenance as VERIFIED evidence, not merely recorded

> **VERDICT UP FRONT: this carve STOPS AND REPORTS.** B004's verified half
> **cannot be built without schema surface** — one new `ReasonCode`, therefore a
> verdict-schema major bump — and it is additionally blocked on a **ciu-side
> semantic defect** that makes its only green branch unreachable on every host in
> this estate. §5 states the schema question, §8 states the reachability problem,
> and §9 shows the measurements behind both. The complete design is carved anyway,
> conditional on two named authority decisions, so that an implementer can execute
> the day both clear. **Nothing in §6 may be started before §3.0's two gates pass.**

---

## 1. The problem

A consumer running an integration or end-to-end lane (assay scope S3/S4) gets a
verdict artifact that says its command passed, its changed lines were covered and
its mutants were killed — and none of that says *which build of the software was
actually running while the tests talked to it*. The containers under test were
started earlier by a separate tool, possibly from an earlier commit, possibly from
an image nobody rebuilt; a green artifact from that run reads as a fact about the
code in the repository while being a fact about whatever image happened to be
deployed. assay ships the *recorded* half of the answer already (A-254/A-255: a
lane can put a caller-supplied revision string into `env_effective` verbatim, on
every outcome), but a value the caller put in the environment is an assertion by
the caller, not an attestation about the artifact, and a consumer auditing the
verdict afterwards has no mechanical way to tell a truthful assertion from a stale
or invented one. The consumer's request is for the artifact to carry evidence that
*something outside assay actually compared the running images against the commit
under test and said so*, in a form that cannot silently read as "checked" when
nothing was checked.

---

## 2. The exact property this capability claims

**For a lane declaring `(adjudicated, "image-provenance")`, an `evidence[]` entry
with `status: "PASS"` means — and means only — that assay read one
`ciu provenance --json` document at `schema_version: 1` which reported
`overall: "verified-match"` and whose `commit_under_test` is a lowercase-hex
abbreviation of the same commit assay resolved as HEAD for this run; that is, ciu
compared the `org.opencontainers.image.revision` label of every running container
in its own compose project against its own repository's HEAD, found at least one
container whose label agreed and none whose label disagreed, and assay confirmed
the commit ciu was talking about is the commit assay measured.**

It does **not** give any of the following. Each line is measured in §9, not asserted.

- **It does not prove the container assay itself ran in was built from the commit
  under test.** ciu's `verified-match` needs only *some* labelled container to
  agree; unlabelled containers are counted neither for nor against. Measured: on
  this host `dstdns-98535c-test-runner` — the container an S3/S4 assay lane runs
  inside — is `"status": "unlabelled"`, so it contributes nothing to the verdict
  that is supposed to describe it (M2, M9).
- **It does not prove the document describes *this* run.** `ciu provenance --json`
  emits no timestamp, no nonce, no run id and no sequence number (M1). The commit
  abbreviation is the only binding assay can perform, so a `verified-match`
  document captured at commit *X* satisfies every later lane run at commit *X*,
  including runs after the containers were stopped, replaced or rebuilt.
- **It does not prove the containers still existed when the lane's command ran,**
  or that the lane's command talked to those containers rather than to something
  else. assay never sees the containers (A-030).
- **The identity binding is an abbreviation, not a hash comparison.** ciu emits
  `git rev-parse --short=8 HEAD` (M10), so assay can only check that ciu's string
  is a hex prefix of assay's 40-character HEAD. Eight hex digits is ~2^32 of the
  identity space, and assay has no way to ask ciu for more.
- **assay verified nothing about images, labels or the daemon.** The entry carries
  `verified_by_assay: false`. The judgement is ciu's; assay's contribution is the
  declared mapping from ciu's closed vocabulary to an outcome, and the commit
  binding.
- **It does not enforce.** A non-green document does not render `FAIL`. It renders
  the declared evidence `NO_MEASUREMENT`, which outranks `FAIL` in the rollup and
  therefore does prevent an overall `PASS` — but the artifact says *"provenance was
  not verified"*, never *"you are running the wrong code"*. Enforcement remains
  what §B004 already called it: a further step, not this one.
- **It does not distinguish *why* provenance failed, inside the verdict.** All five
  of ciu's non-green `overall` values collapse to one terminal (§3.4). The
  discriminating detail exists only in the input document, which the lane retains.
- **It says nothing at all about unlabelled containers.** On this host that is 16
  of 20 (M2).
- **It is not a same-instance check.** A-256 stands: assay does not compare
  `instance` against anything, because no assay call site can derive the expected
  value (§4.5).

---

## 3. The design

### 3.0 Two gates that must pass before any of §6 is started

Both are authority decisions, not implementation choices. An implementer who finds
either unresolved must stop and report, not route around it.

**GATE 1 — verdict schema surface (operator decision).** This design needs exactly
one new `ReasonCode` value. §5.2 states the exact minimum and why every
zero-schema alternative examined was rejected as dishonest. The brief that
commissioned this carve says B004 must not require a verdict-schema bump; **that
premise is false**, and §5.3 explains why §B004's own text believed otherwise.
Until a `decisions.md` row accepts the widening and a schema version to carry it,
implementation is mechanically blocked: there is no legal `(outcome, reason_code)`
pair for four of the six documents ciu can produce.

**GATE 2 — ciu must be able to produce a green document at all (external
prerequisite).** Measured (M2, M3, M4, M6): ciu compares *every* running
container's OCI revision label against its own repository's short hash, including
third-party vendor images that stamp their *own* upstream repository's revision.
On this host that yields four permanent false mismatches — `otel-collector` at
`1400269f8ace...`, `timescaledb-ha` at `refs/heads/master`, `skywalking-ui` at
`9fc54aa1...` — none of which has anything to do with dstdns's code, and
`overall` is therefore pinned at `"mismatch"` regardless of what dstdns builds.
`verified-match` is consequently **unreachable on this host**, and the PASS branch
of this design cannot be exercised against real output anywhere in this estate.
ciu needs a scoping change — restrict the comparison to images ciu itself produced,
e.g. by having `bake` stamp a ciu-owned marker label and having
`verify_running_provenance` consider only containers carrying it — filed as a
CIU-20 follow-on. **Its implementation is ciu's design decision, not assay's**; the
requirement assay needs is only that a correctly deployed instance can produce
`overall: "verified-match"`.

### 3.1 Config surface — exact TOML keys and spelling rules

The shape mirrors the shipped Tier-3 pair (`judge.attestation_dir` +
`judge.evidence`, A-209) exactly, because that pair already solved this problem:
a declared input directory plus a declared closed list of identities, with **no
location ever derived** and no CLI-supplied per-invocation path.

```toml
schema_version = 1                      # lane file version -- see §5.5

[lanes.S3]
argv = ["python", "-m", "pytest", "tests/integration", "-q"]
rigor = ["R0"]
scope = "S3"

[lanes.S3.judge]
adjudication_dir = "artifacts/adjudicated"   # NEW KEY

[[lanes.S3.judge.evidence]]
source = "adjudicated"                       # NEWLY ACCEPTED VALUE
key    = "image-provenance"
```

**`judge.adjudication_dir`** — one project-relative POSIX directory spelling.
Grammar is **byte-identical to `attestation_dir`'s**, and is implemented by
promoting `attestation.py::_validate_attestation_dir` to a shared
`_validate_evidence_dir(value, field_name)` rather than writing a second copy:
non-empty `str`; 1..`MAX_ATTESTATION_DIR_BYTES` UTF-8 bytes; not absolute; no
control characters; `PurePosixPath(value).as_posix() == value` (canonical
spelling); no `.` or `..` component, including a bare `.`; at most
`MAX_ATTESTATION_DIR_COMPONENTS` components. **A-271 is satisfied by identity, not
by exception:** this is a declared list-adjacent path with the same aggregation
exposure profile as `attestation_dir`, so it takes the same strict grammar, and no
new asymmetry is introduced.

**`judge.evidence[].source`** — `config._EVIDENCE_SOURCES` widens from
`frozenset({"attested"})` to `frozenset({"attested", "adjudicated"})`. Measured:
today the loader refuses `source = "adjudicated"` outright
(`config.py:1372`), so this widening is what makes B004 declarable at all (M11).
It is purely additive: every lane that loads today loads byte-identically after.

**`judge.evidence[].key`** — unchanged grammar,
`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. For `source = "adjudicated"` the key
additionally **selects the adjudicator** from the registry A-078 deferred (§3.2),
and a key absent from that registry is refused at **load**.

**Pairing rule.** `config.py:1160` currently enforces
`has_attestation_dir == has_evidence`. That rule is correct only while every
declarable source is `attested`. It is replaced by a per-source rule:
`attestation_dir` is present iff at least one declared entry has
`source = "attested"`, and `adjudication_dir` is present iff at least one has
`source = "adjudicated"`. A lane may declare both, one, or neither. Refusals are
`ERROR`/`BAD_LANE_CONFIG` at load, consistent with A-254's precedent that
`BAD_LANE_CONFIG` is the terminal meaning *"the lane as declared cannot be honoured
here"*. No existing lane changes classification: today `has_evidence` implies
all-attested, so the old and new rules agree on every currently loadable file.

**Document location.** `<adjudication_dir>/<key>.json`, i.e.
`artifacts/adjudicated/image-provenance.json` for the example above. Read exactly
once through `safeio.read_bounded_input(project_root, relative_path, limit=…)`,
which is the existing descriptor-walk seam: `O_NOFOLLOW` at every step, regular
file only, no path-precheck-then-reopen, bounded before decoding.

**Byte ceiling.** `MAX_ADJUDICATION_BYTES = 1_048_576`. Justified by measurement,
not by taste: the real 20-container document is 2 377 bytes (M2), so 1 MiB admits
roughly 8 800 containers, and a document larger than that is not a provenance
verdict.

**Producing the document is the caller's job, outside assay.** The consumer's
harness runs, on the host, before `docker run`:

```sh
ciu provenance --json > "$PROJECT/artifacts/adjudicated/image-provenance.json" || true
```

The `|| true` is load-bearing and must be documented: `ciu provenance --json`
exits **2** on `overall: "mismatch"` while still writing a complete, valid document
to stdout (M2). The document, never the exit code, is the evidence — see §3.5.

### 3.2 The adjudicator registry (A-078)

A new module `src/assay/adjudication.py` holds:

```
ADJUDICATORS: Mapping[str, Adjudicator]   # key -> callable, one entry: "image-provenance"
```

`Adjudicator` is a callable `(document_bytes, head) -> (Outcome, ReasonCode | None)`.
The registry is consulted at **load** (unknown key ⇒ `ERROR`/`BAD_LANE_CONFIG`) and
at **run** (to dispatch). It is deliberately a dict of one entry rather than a
plugin discovery mechanism — A-078's objection to a registry was that a
zero-integration one "could only validate its own empty set", which a one-entry
registry with a reachable unknown-key refusal does not suffer from.

**Reachability of the unknown-key refusal, stated because this project forbids
`pragma: no cover`:** any `assay.toml` containing
`source = "adjudicated"`, `key = "no-such-adjudicator"` is a well-formed, fully
loadable file that reaches it. The check is reachable from a real config, not only
from a hand-built `Lane` object.

### 3.3 The closed vocabulary assay accepts, and what it deliberately does not check

Every rule below is a claim about **ciu's** output and was measured against ciu
6.0.3 running on this host (§9), per A-272's binding lesson.

assay validates **exactly what it consumes, and nothing else**:

| rule | consequence on violation |
|---|---|
| the document is a JSON object | `ERROR` / `FORMAT_MISMATCH` |
| `schema_version` is the integer `1` | `ERROR` / `FORMAT_MISMATCH` |
| `overall` is present and is one of the six closed values below | `ERROR` / `FORMAT_MISMATCH` |
| **on the `verified-match` path only:** `commit_under_test` is a string matching `^[0-9a-f]{8,40}$` | `ERROR` / `FORMAT_MISMATCH` |

The six closed `overall` values, read from ciu's own producer
(`ciu/src/ciu/deploy.py:600-690` and `cli.py:461-478`) and confirmed by
measurement: `verified-match`, `mismatch`, `not-verified-dirty`,
`not-verified-unknown`, `not-verified-no-evidence`, `refused-no-identity`.
An unrecognised value is **refused, not guessed** — §B004's closed-vocabulary
discipline, preserved.

**assay asserts nothing about `containers`, `status`, `labelled_revision`,
`image`, `tree_state` or `instance`.** This is the single most important
design rule in this carve and it is A-272's lesson applied before the fact rather
than after: every plausible tightening was measured and every one would have
refused real ciu output.

- `labelled_revision` is **not** a sha grammar. Real value measured:
  `"refs/heads/master"` (M2, M5). A `^[0-9a-f]{40}$` rule — the obvious one —
  renders a valid document `ERROR`/`UNREADABLE_ARTIFACT`.
- `image` is **not** `name:tag`. Real value measured: `"6cf88efc53e8"`, a bare
  image id (M2).
- `containers` is **`null`**, not `[]`, whenever enumeration could not run or the
  tree was dirty or the directory was not a checkout. Measured twice (M1, M8). ciu
  documents this distinction as load-bearing — collapsing them was CIU-20's own
  original defect — so a `list`-typed rule refuses real output.
- `tree_state` carries at least `"clean"`, `"dirty"`, `"not-a-checkout"` and is
  `null` on the refusal path. Closing that enum buys assay nothing, because
  `overall` already encodes the decision, and can only manufacture false refusals.
- `status` per container carries at least `"unlabelled"`, `"mismatch"`, `"match"`.
  Same reasoning.

The division of labour is Tier 2's definition: **ciu decides, assay adjudicates the
decision.** assay re-deriving `overall` from `containers` would be assay computing
a provenance verdict, which A-030 says it structurally cannot do.

### 3.4 The mechanism, and the refusal set with its exact reason code

Runs in `cli._run_reserved`, in the existing documented sequence
(`lane/output reserved → deadline → HEAD → attestation → adapter → command → emit
once`), immediately after `attestation.load_attested_evidence` and before adapter
resolution. HEAD is already resolved at that point, which is what makes the commit
binding derivable at the call site.

| # | condition | outcome / reason_code | reachable from |
|---|---|---|---|
| 1 | declared `source="adjudicated"` with a key not in `ADJUDICATORS` | `ERROR` / `BAD_LANE_CONFIG` (load) | any `assay.toml` with `key = "no-such-adjudicator"` |
| 2 | an adjudicated entry declared with no `adjudication_dir` (or the converse) | `ERROR` / `BAD_LANE_CONFIG` (load) | any `assay.toml` omitting one of the pair |
| 3 | `adjudication_dir` violates the path grammar | `ERROR` / `BAD_LANE_CONFIG` (load) | `adjudication_dir = "/etc"`, `"./x"`, `"a/../b"` |
| 4 | document absent — `safeio.read_bounded_input` returns `None` | `NO_MEASUREMENT` / **`PROVENANCE_UNVERIFIED`** | never run ciu; delete the file |
| 5 | document present but unreadable: symlink, non-directory parent, non-regular final object, permission failure, race, read exceeds 1 MiB, invalid UTF-8, invalid JSON | `ERROR` / `UNREADABLE_ARTIFACT` | `ln -s /dev/null` the path; write 2 MiB; write `{` |
| 6 | not a JSON object, or `schema_version != 1`, or `overall` unrecognised, or (green path) `commit_under_test` not `^[0-9a-f]{8,40}$` | `ERROR` / `FORMAT_MISMATCH` | edit a real capture |
| 7 | `overall == "verified-match"` but `commit_under_test` is not a prefix of assay's HEAD | `NO_MEASUREMENT` / **`PROVENANCE_UNVERIFIED`** | reuse yesterday's capture; **or hit dstdns's concurrent committer (M8)** |
| 8 | `overall` ∈ {`mismatch`, `not-verified-dirty`, `not-verified-unknown`, `not-verified-no-evidence`, `refused-no-identity`} | `NO_MEASUREMENT` / **`PROVENANCE_UNVERIFIED`** | all five measured or trivially producible (M1–M4, M8) |
| 9 | `overall == "verified-match"` and the commit binds | `PASS`, no reason code | **NOT REACHABLE on this host — GATE 2, §8.1** |
| 10 | lane deadline expires during adjudication | `BUDGET_EXCEEDED` / `LANE_TIMEOUT` | existing `_timed_out_evidence` path, A-213 |

Row 4 is `PROVENANCE_UNVERIFIED` and not `UNREADABLE_ARTIFACT` on purpose:
`safeio`'s own contract distinguishes *absent* (`None`) from *present but
untrustworthy* (raise), and calling an absent file unreadable is a false diagnosis.
It is also not `MISSING_ATTESTATION` — measured, `Evidence.__post_init__`
(`verdict.py:2077-2081`) refuses `MISSING_ATTESTATION`/`STALE_ATTESTATION` on any
non-`attested` source, which is correct and must stay.

Row 8's collapse of five distinguishable ciu states into one terminal is forced,
not chosen: see §4.2.

### 3.5 How a mismatch is surfaced, given no payload slot exists

**Measured constraint (M11):** the adjudicated `Evidence` shape has **no payload**.
`Evidence.__post_init__` explicitly refuses `producer`, `attested_commit` and
`reviewed_paths` for `source == "adjudicated"`
(*"attestation payload belongs only to attested evidence"*), and the verdict
schema's `evidence` `$def` mirrors it. The complete adjudicated wire surface is
therefore `source`, `key`, `status`, `verified_by_assay`, and optionally
`reason_code`. There is nowhere to put "which container", "which label" or "which
commit ciu saw" without adding a field — and adding a field is exactly the hollow
schema blessing A-255 refused.

So the surfacing is threefold, and none of it is a new verdict field:

1. **In the verdict:** the entry itself —
   `{"source":"adjudicated","key":"image-provenance","status":"NO_MEASUREMENT",
   "verified_by_assay":false,"reason_code":"PROVENANCE_UNVERIFIED"}` — plus the
   rollup consequence. `NO_MEASUREMENT` outranks `FAIL`, so the lane cannot report
   `PASS`, and a consumer reading only the top-level `outcome` still gets the
   right gate decision.
2. **In the human stream:** `assay run` prints one line naming the input path and
   the exact `overall` value it read, so an operator is not left guessing which of
   the five states occurred. This is stderr prose, not artifact content.
3. **In the input document itself,** which the consumer's harness retains beside
   the verdict. It carries the per-container detail verbatim, produced by the tool
   that is authoritative for it.

**Explicitly refused: encoding the state in the `key`.** The key is declared by the
lane and the model enforces exact cross-array coverage between `declared_evidence`
and `evidence` (`runner.py:836`, `verify.py:362`), so an emitted key that differed
from the declared one would fail the artifact's own verifier. It is an identity,
not a payload, and must stay one.

---

## 4. Why this shape and not the alternatives

### 4.1 Why `mismatch` maps to NO_MEASUREMENT and not to FAIL — §B004's sketch, dropped

§B004 sketches `mismatch → FAIL`. Dropped, for two independent reasons.

**It is not representable.** Measured (M11): `verdict._check_reason_code` requires a
reason code for *every* non-PASS outcome and refuses any code outside that
outcome's closed set. The seven `FAIL` codes are `UNCOVERED_LINES`,
`UNCOVERED_BRANCHES`, `EXCLUDED_LINES`, `UNCLASSIFIED_LINES`, `MUTANTS_SURVIVED`,
`CANARY_SURVIVED`, `COMMAND_FAILED`. Not one of them truthfully names *"the
provenance tool reported that the running images were built from a different
commit"*. A `FAIL` mapping therefore requires a new `FAIL` code — which is
precisely the "enforced" step §B004's own table lists as **not proposed**. The
sketch is internally inconsistent with the section that contains it (§5.3).

**`NO_MEASUREMENT` is the *more* correct terminal anyway.** The DESIGN-GUIDE's own
doctrine for `NO_MEASUREMENT` (§6, "Nailing NO MEASUREMENT") is: *"the delta being
judged is not the delta under test"*, and that is why it outranks `FAIL` — it
"invalidates everything computed beneath". A provenance mismatch says exactly
that: the artifact the lane measured is not the artifact the commit describes, so
every coverage, mutation and canary number below it is about something else. A
`FAIL` would additionally assert a defect in the consumer's *code*, which the
evidence does not support. `NO_MEASUREMENT` is both honest and strictly stronger in
the rollup.

### 4.2 Why one new reason code and not four

The alternative is a code per ciu state — an adverse `FAIL` code for `mismatch`
plus one or more `NO_MEASUREMENT` codes for the `not-verified-*`/`refused-*`
family. Rejected: it multiplies the closed-enum widening (each value is a
consumer-visible compatibility cost, A-138/A-170) to buy a distinction that has a
better home. The five non-green states differ in *why* ciu could not attest, and
that "why" is fully recorded, verbatim and per-container, in the input document the
consumer already keeps. One terminal, one meaning — *"assay read a provenance
document and it did not attest a match for this commit"* — is exactly true for all
five, and does not require assay to re-tell ciu's story in assay's vocabulary.

**Alternative rejected: reuse an existing code.** Every candidate was examined and
each is a second meaning for a name that already has one, which is the collision
A-145 and A-268(a) exist to prevent:
`DIRTY_TREE` means *assay's own snapshot tree*, not ciu's host tree;
`HEAD_CHANGED` means *the lane's command moved HEAD*, and its docstring says so;
`GIT_FAILED` means *assay's own git call failed*;
`EMPTY_COVERAGE`/`TARGET_NOT_MEASURED` are coverage-artifact terminals;
`MISSING_EXTERNAL_TOOL` is reserved for P27 and is bound in writing to
`LanguageAdapter.external_tools`, so claiming it would both overload it and steal a
reservation;
`BAD_LANE_CONFIG` would blame the consumer's lane for ciu's configuration.
Stacking five such stretches is the "attestation stronger than its mechanism"
defect three wave-1 review rounds already killed once.

### 4.3 Why a declared directory and not §B004's `--provenance-json <path>`

Dropped. A CLI flag lets a caller point the same lane at a different document on
every invocation, which is the ambient, undeclared input B006 and A-266/A-269 spent
a whole wave removing; and it puts an evidence location outside the lane file,
where the artifact cannot record that it was declared. The shipped Tier-3 pair
already solves this exact problem with `attestation_dir` + `key`, and reusing its
shape means one grammar, one validator and one mental model instead of two.

### 4.4 Why A-204 is not the governing ruling here

§B004 cites A-204 ("byte-copy, never interpret"). Corrected: A-204 governs the
*independent Topos comparator's* wrapper, whose whole point is that it must not
become a shared oracle with assay. assay itself interprets its inputs — that is
what every coverage parser does. What survives from the citation, and is kept, is
narrower and still important: **assay consumes `--json` only and never the prose
CLI output.** Measured (M7), the prose form writes a multi-line human message to
stderr and nothing to stdout, and `--ignore-mismatch` makes it print a warning
immediately followed by `provenance OK` — a contradiction ciu documents as
deliberate backward compatibility. No parser should go near it.

### 4.5 Why `instance` is not compared

Dropped. assay has no independent knowledge of the expected instance id; the only
way to obtain one is a lane declaring `env_required = ["CIU_INSTANCE_ID"]`, which
makes the check present for some lanes and silently absent for others — a check
whose absence is invisible, i.e. A-025's "absence of a signal is never a positive
fact" at the config layer. This is wave-1 lesson 3 exactly: *no call site can
derive the value*. A-256 already ruled same-instance comparison to be process
discipline over `env_effective`, belonging to the future verdict comparer, and this
carve does not disturb that.

### 4.6 Why not defer the whole thing to CIU-21 instead

CIU-21 (inject the image's own baked label as an env var) would make the *recorded*
value ciu-attested with zero assay work, per A-255's ladder. It is a good step and
it is not this one: an env var still arrives through the caller's environment, so
the artifact records "this ran with this variable set to this" and nothing about
whether anyone compared it to anything. The adjudicated route is the only one that
records *a comparison having been performed*, which is what the word "verified"
buys. Both should ship; neither substitutes for the other.

---

## 5. What it records in the verdict, and the schema question

### 5.1 Field by field, with the exact producing call site

**No new verdict field is added, and none should be.** The complete record is one
`Evidence` entry plus its mandatory `declared_evidence` sibling.

| wire field | value | exact producing call site |
|---|---|---|
| `declared_evidence[i].source` | `"adjudicated"` — verbatim from the lane | `cli._declared_evidence` (`cli.py:284`), unchanged; it already copies `item.source` from `lane.judge.evidence` |
| `declared_evidence[i].key` | the declared key, verbatim | same, unchanged |
| `evidence[i].source` | the constant `"adjudicated"` | **NEW** `adjudication.evaluate_provenance` |
| `evidence[i].key` | the declared key, threaded from the declaration | **NEW** `adjudication.load_adjudicated_evidence`, mirroring `attestation.load_attested_evidence`'s "one result per declaration, same order" contract |
| `evidence[i].status` | derived from the parsed `overall` and the HEAD prefix comparison, per §3.4's table | **NEW** `adjudication.evaluate_provenance`; both inputs are in scope — the document bytes from `safeio`, `head` from the CLI sequence, which resolves HEAD before attestation |
| `evidence[i].verified_by_assay` | the constant `False` | same; assay did not verify the images, ciu did |
| `evidence[i].reason_code` | per §3.4's table; omitted on `PASS` | same |
| `evidence[i]` on lane timeout | `BUDGET_EXCEEDED`/`LANE_TIMEOUT` | `cli._timed_out_evidence` (`cli.py:296`) — **already source-agnostic**, it copies `item.source` from the declaration, so it needs no change |

Every value is derivable at its named call site, and there is no path on which
assay must invent one. The wave-1 defect this table exists to prevent — a field
ruled into the verdict that no producer can populate — does not arise, because no
field is added.

### 5.2 Schema surface needed: **YES — exactly one new `ReasonCode`**

> **`PROVENANCE_UNVERIFIED`**, in the `NO_MEASUREMENT` set.
> *assay read a declared provenance document and it did not attest a verified match
> for the commit under test — because the document was absent, because the producing
> tool reported a non-green verdict, or because the document describes a different
> commit. Payload-free. The discriminating detail lives in the input document.*

Exact touch points:

- `src/assay/errors.py` — the `ReasonCode` enum member and its entry in
  `REASON_CODES[Outcome.NO_MEASUREMENT]`.
- `src/assay/schemas/verdict.schema.json` — `$defs/reason_code` enum **and**
  `$defs/reason_codes/NO_MEASUREMENT` enum.

Per A-138/A-170 and A-254's explicit reasoning, a closed-enum widening is *"a
widening that every consumer's schema copy would then reject"*, so it is
**v6→v7**, a major bump with the migration and consumer-repin cost that implies.
The one route that avoids a bump is the one this estate already used for
`MISSING_EXTERNAL_TOOL` (A-013/A-086/A-144): **reserve the value in the enum during
some other bump, and render it later for free.** If the operator would rather not
spend a v7 on B004 alone, the correct move is to reserve `PROVENANCE_UNVERIFIED`
in the *next* bump B001/P34 or B007 needs, and land B004's producer afterwards at
zero schema cost. That is a sequencing decision, and it belongs to the operator.

**What is NOT needed** — checked, not assumed:

- **No new `evidence_source` value.** Measured (M11): the verdict schema's
  `$defs/evidence_source` enum already contains `"adjudicated"`
  (`verdict.schema.json:505`), and `verdict.EVIDENCE_SOURCES` already carries it
  (`verdict.py:224`). A-034/A-078 reserved the slot in v2 for exactly this day, and
  the reservation holds.
- **No new verdict field** of any kind.
- **No change to `declared_evidence`'s shape.**

### 5.3 Why §B004 believed no schema change was needed — a correction to the backlog

§B004 states *"no verdict-schema change was needed and none should be added"* and
its own table puts a new `ReasonCode` only against the **Enforced** row. Both
statements are true of the route they were written about — A-255's `env_effective`
route, where recording a provenance variable genuinely needs nothing — and both are
false of the adjudicated route. The confusion is a conflation of *field* with
*enum value*: A-255 correctly refused a new `provenance` **object**, and §B004 read
that as "no schema surface at all". The adjudicated route needs no field and one
enum value, because the `Evidence` shape has no payload and every non-`PASS`
outcome is required by `_check_reason_code` to name a code. **§B004's own sketched
mapping (`mismatch → FAIL`, `not-verified-*` → NO_MEASUREMENT-class) is not
buildable at zero schema cost; it *is* the enum widening the same section says is
not proposed.** This carve recommends a `decisions.md` row recording that
correction, so the next reader of the backlog is not misled the same way.

### 5.4 The one latent hole this integration must close

Measured (M11): the verdict schema constrains `verified_by_assay` to `false` only
when `source == "attested"`. For `adjudicated` it is an unconstrained boolean, and
`Evidence.__post_init__` does not constrain it either. Nothing has ever produced an
adjudicated entry, so nothing has ever exercised it — but as written, a Tier-2
result could ship `verified_by_assay: true` and be schema-legal, which would let
adjudicated evidence read as computed. B004 is the first integration and must close
it. **Close it in `Evidence.__post_init__` only** (adjudicated ⇒
`verified_by_assay is False`), which is zero schema surface. Tightening the JSON
schema's `else` branch to `{"verified_by_assay": {"const": false}}` is the more
complete fix and is *technically* a narrowing rather than a widening — no document
that has ever existed changes classification — but a narrowing is still a
compatibility fact under A-029, so it is **deferred to whichever bump carries
`PROVENANCE_UNVERIFIED`** rather than smuggled in.

### 5.5 The lane-side widening, and the question it raises

`_EVIDENCE_SOURCES` gains `"adjudicated"` and `judge.adjudication_dir` is a new
optional key. Both are strictly additive: **every lane file that loads under
`LANE_SCHEMA_VERSION = 2` today loads identically afterwards**, so no consumer
migration is forced. Whether an additive optional key nonetheless bumps
`LANE_SCHEMA_VERSION` 2→3 is an operator call this carve does not make; it flags
it because the answer changes §6's doc-example obligations (A-270 check 1 asserts
every TOML example declares the *current* `LANE_SCHEMA_VERSION`, so a bump means
touching every example in all three documents).

---

## 6. Work items, in dependency order

Nothing below may start before §3.0's Gates 1 and 2 pass.

**W0 — record the authority decisions.** No code.
Files: `nyxloom-trove/decisions.md`, `nyxloom-trove/4-backlog.md`.
Adds a row accepting `PROVENANCE_UNVERIFIED` and its schema version, a row
recording §5.3's correction to §B004, and a row recording §4.1's replacement of
`mismatch → FAIL` with `mismatch → NO_MEASUREMENT`. Also corrects A-O12's false
claim that assay records `declared_unverified` — measured in §B004 and re-confirmed
here: that string appears nowhere in `src/`, `docs/` or the schema.
**Blocked until Gate 1 is ruled.**

**W1 — the ciu prerequisite.** No assay code. A CIU ticket for §3.0 Gate 2:
provenance must be scoped to images ciu produced, so a correctly deployed instance
can emit `verified-match`. Assay work item W6's PASS oracle depends on it.
**Not assay's to implement.**

**W2 — the reason code.** Files: `src/assay/errors.py`,
`src/assay/schemas/verdict.schema.json`, plus whatever migration the chosen bump
requires. Tests: `tests/test_errors.py`, `tests/test_verdict_reason_codes.py`,
`tests/test_verdict_schema_rejects.py`, `tests/test_verify_layer_independence.py`
(A-182: the raw verifier states the pairing rule independently of the schema, so
both layers gain the value and a test proves they agree).
**Docs (A-270): none** — a reason code is not a value a consumer types into a lane.

**W3 — the config surface.** Files: `src/assay/config.py` (widen
`_EVIDENCE_SOURCES`; add `adjudication_dir`; replace the `has_attestation_dir ==
has_evidence` rule with the per-source rule; export the sources set publicly for
W7's vocabulary test), `src/assay/attestation.py` (promote
`_validate_attestation_dir` to a shared `_validate_evidence_dir`).
Tests: `tests/test_config_accept.py`, `tests/test_config_reject.py`,
`tests/test_config_vocabularies.py`, `tests/test_attestation_load_declared.py`.
Must include: an adjudicated entry without `adjudication_dir` refused; an attested
entry without `attestation_dir` refused; a lane declaring both sources accepted;
every `attestation_dir` grammar refusal re-asserted for `adjudication_dir` through
the shared validator.
**Docs (A-270): `README.md`, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`** — this
item adds a public config key and a closed-vocabulary value a consumer types.

**W4 — the adjudicator registry and the provenance adjudicator.** Files:
**new** `src/assay/adjudication.py`; `src/assay/config.py` (load-time unknown-key
refusal). Tests: **new** `tests/test_adjudication_registry.py`,
**new** `tests/test_adjudication_provenance_parse.py`.
Covers §3.3's four validation rules, §3.4's rows 1 and 4–8, and — critically — a
test per §3.3 bullet proving assay does **not** refuse the measured real shapes
(`"refs/heads/master"`, a bare image id, `containers: null`).
**Frozen assets, captured for this carve and byte-exact from real ciu 6.0.3
output** (see §9): `nyxloom-trove/carve-assets/W2/ciu-provenance-mismatch.json`,
`.../ciu-provenance-not-verified-unknown.json`,
`.../ciu-provenance-not-verified-dirty.json`.
**Docs (A-270): none directly** — W3 and W5 carry the user-facing surface.

**W5 — CLI wiring and the verdict.** Files: `src/assay/cli.py` (call
`adjudication.load_adjudicated_evidence` at the documented point; the one human
stderr line of §3.5), `src/assay/verdict.py` (§5.4's `__post_init__` tightening).
Tests: `tests/test_runner_assemble_verdict_evidence.py`,
`tests/test_verdict_evidence_artifacts.py`, `tests/test_cli_run.py`,
**new** `tests/test_adjudication_pipeline_integration.py` (mirroring
`test_attestation_pipeline_integration.py`), plus a timeout test proving
`_timed_out_evidence` renders an adjudicated declaration correctly with no change
to that function.
**Docs (A-270): `README.md`, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`.**
Specifically: README's Tier-2 bullet currently says adjudicated evidence is how
assay integrates with scanners by *"invok[ing] a declared tool"*, and
DESIGN-GUIDE §3's tier table says Tier 2 **"invokes** a declared third-party tool".
**Both are falsified by this integration** — A-030 forbids assay invoking ciu, and
at S3/S4 assay is inside the container where the docker socket is not reachable, so
the tool's output arrives as a declared file. §3's table and the README bullet must
be amended to "invokes, or consumes the declared structured output of, a declared
third-party tool", with the reason stated in the DESIGN-GUIDE and linked from the
README. CONSUMERS.md gains a pasteable worked example including the
`|| true` and the reason for it (§3.1).

**W6 — acceptance oracles.** Files: tests only, per §7.
**Depends on W1 for O7 and O8 only.**

**W7 — the documentation gate.** Files: `tests/test_docs_examples_and_vocabulary.py`.
A-270 check 2 derives its vocabularies from shipped modules and currently covers
four (`SNAPSHOT_SELECTIONS`, `JUDGE_MODES`, `RIGOR_LEVELS`, `FORMAT_REGISTRY`).
`judge.evidence[].source` is a closed public vocabulary a consumer types and is
**not** among them — a pre-existing gap this item closes. Add a fifth derived
vocabulary from the exported evidence-sources set, assert both values appear in
the three documents, and extend
`test_derived_vocabularies_are_not_accidentally_identical_placeholders` to include
it.
**Docs (A-270): `README.md`, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`** — the
new test will fail until they name both `attested` and `adjudicated`.

---

## 7. Acceptance oracles

Each states the exact command, the exact expected output, and the exact observable
that differs if the feature is absent or broken.

**O1 — real output, non-green, live.** *This is the oracle that consumes real
`ciu provenance --json` output, per wave-1 lesson 5.*
Command: from `/workspaces/dstdns`, `ciu provenance --json > $P/artifacts/adjudicated/image-provenance.json || true`;
then `assay run S3 --verdict-json v.json` for the §3.1 lane.
Expected: `v.json` `evidence[0]` is exactly
`{"source":"adjudicated","key":"image-provenance","status":"NO_MEASUREMENT","verified_by_assay":false,"reason_code":"PROVENANCE_UNVERIFIED"}`,
top-level `outcome` is `NO_MEASUREMENT`, and `assay verify v.json` is clean.
Differs if absent: **without B004 the lane does not run at all** — the loader
refuses with `'judge.evidence[0].source' must be one of ['attested'], got
'adjudicated'`, a different exit code and a different document (M11).
Differs if broken: a parser that tolerates a non-green `overall` renders `PASS` and
the top-level `outcome` becomes `PASS`.

**O2 — the frozen real capture, against tightening.**
Command: parse `carve-assets/W2/ciu-provenance-mismatch.json` — byte-exact real ciu
6.0.3 output, 2 377 bytes — through the shipped adjudicator.
Expected: `NO_MEASUREMENT`/`PROVENANCE_UNVERIFIED`.
Differs if broken: **any** of the tightenings §3.3 forbids turns this into
`ERROR`/`FORMAT_MISMATCH` or `ERROR`/`UNREADABLE_ARTIFACT`. A `^[0-9a-f]{40}$` rule
on `labelled_revision` fails on `"refs/heads/master"`; a `name:tag` rule on `image`
fails on `"6cf88efc53e8"`; a closed `status` enum missing `"unlabelled"` fails on
16 of the 20 members. This is the A-272 regression, caught before it ships.

**O3 — the exit code is not the verdict.**
Command: `cd /tmp && ciu provenance --json > doc.json; echo $?` then adjudicate
`doc.json`.
Expected: **`$?` is 0** while `overall` is `"not-verified-unknown"` and
`containers` is `null`; the adjudicator returns
`NO_MEASUREMENT`/`PROVENANCE_UNVERIFIED`.
Differs if broken: any design that consults the producer's exit status — or that
a harness author wires up with `set -e` and a status check — treats this document
as success. Measured, not hypothetical (M1): ciu exits 0 for a document that
verified nothing, and exits 0 for an actual `mismatch` under `--ignore-mismatch`
(M6). The frozen asset is `carve-assets/W2/ciu-provenance-not-verified-unknown.json`.

**O4 — staleness / wrong commit.**
Command: adjudicate a `verified-match` document whose `commit_under_test` is
`"deadbeef"` against a HEAD that does not start with it.
Expected: `NO_MEASUREMENT`/`PROVENANCE_UNVERIFIED`.
Differs if the binding check is absent: `PASS` — a document from any earlier run at
any commit satisfies the lane forever.

**O5 — an unrecognised `overall` is refused, not guessed.**
Command: adjudicate a real capture with `overall` rewritten to `"probably-fine"`.
Expected: `ERROR`/`FORMAT_MISMATCH`, which is a **different** terminal from every
known non-green state.
Differs if broken: a `mapping.get(overall, NO_MEASUREMENT)` default renders
`NO_MEASUREMENT`/`PROVENANCE_UNVERIFIED`, indistinguishable from a genuine
non-green verdict, and a future ciu vocabulary addition would be silently
absorbed. The two-terminal split is what makes this observable.

**O6 — the unknown adjudicator key is refused at load.**
Command: `assay run` a lane declaring `source = "adjudicated"`,
`key = "no-such-adjudicator"`.
Expected: `ERROR`/`BAD_LANE_CONFIG`, message naming the registered keys.
Differs if absent: the lane loads and then fails at run time with a less useful
terminal, or dispatches to nothing.

**O7 — the green path. `⚠ FABRICATED INPUT, WITH A STANDING RE-WITNESS
OBLIGATION.`**
Command: adjudicate a hand-written `verified-match` document whose
`commit_under_test` is a real prefix of the test repository's HEAD.
Expected: `PASS`, `verified_by_assay: false`, no reason code.
Differs if broken: any other outcome.
**This oracle knowingly violates wave-1 lesson 5, and it must be labelled as such
in the test's own docstring.** No host in this estate can produce a
`verified-match` document today (§8.1, M2, M4), so there is no real output to
consume. Per A-274's re-witness precedent, the obligation is recorded here: **the
day Gate 2's ciu change lands, this fixture is re-taken from a real run and the
diff reviewed**, never hand-edited toward green.

**O8 — end-to-end green, live. `BLOCKED ON GATE 2.`**
Command: after ciu scopes provenance to its own images: `ciu bake && ciu up &&
ciu provenance --json > …`, then `assay run S3`.
Expected: `evidence[0].status == "PASS"`, top-level `outcome == "PASS"`.
Differs if absent: this is the only oracle that proves the capability's headline
claim against real output, and until Gate 2 it cannot be written. **It must not be
faked, skipped silently, or replaced by O7.**

**O9 — the documentation gate.**
Command: `pytest tests/test_docs_examples_and_vocabulary.py`.
Expected: green, with the new evidence-source vocabulary among the derived sets.
Differs if broken: deleting `adjudicated` from all three documents must fail the
new test; the shipped must-fail control
(`test_a_fabricated_vocabulary_value_is_reported_missing_the_broken_control`)
already proves the membership routine can detect an absent value.

---

## 8. Limitations and deferrals

**8.1 The PASS branch is unreachable on this host, and that is the strongest
argument for not building B004 yet.** Measured (M2, M4): `overall` is pinned at
`"mismatch"` by three vendor images carrying their own upstream revision labels,
and no running dstdns-owned image carries the label at all. Baking dstdns's images
would not fix it, because the vendor containers remain in the same compose project
and one disagreeing label is enough. So the capability's headline state has **no
witness anywhere in this estate**, and O7 must fabricate its input. Wave 1's
lesson 2 — "either a check is reachable and you say from where, or it does not
belong" — applies to the *green* branch here rather than to a refusal, which is
unusual and worse: a capability whose success case nobody can demonstrate. This is
why Gate 2 is a gate and not a caveat.

**8.2 There is no freshness bound.** ciu's document carries no timestamp, nonce or
run identifier (M1, M2), so the commit abbreviation is the only binding available
and it is not a proof of recency. A `verified-match` captured at commit *X* remains
satisfying for every subsequent run at commit *X*. Deferred rather than solved
because the fix belongs to ciu (emit a monotonic or wall-clock stamp), and assay
inventing one — e.g. by stat'ing the file's mtime — would be a check on the
filesystem rather than on the evidence.

**8.3 The commit binding is flaky in this estate, for a reason outside assay.**
Measured (M8): three `ciu provenance --json` invocations minutes apart in
`/workspaces/dstdns` returned `commit_under_test` values `682b5b01`,
`c3e17272-dirty` and `016a2674` — dstdns has a concurrent committer, exactly like
vbpub. So the document is produced against a moving HEAD, and a harness that
captures it even seconds before `assay run` can legitimately produce a document
whose commit no longer matches. Row 7 of §3.4 will therefore fire in normal
operation, not only on stale input. Documented as a consumer-facing operational
fact (CONSUMERS.md), not worked around: the alternative — accepting any commit —
deletes the only binding the design has.

**8.4 The five non-green states are not distinguishable inside the verdict.**
Accepted, per §4.2. The detail is in the retained input document.

**8.5 Enforcement (`FAIL` on mismatch) remains unbuilt and unproposed,** exactly as
§B004 says — and now for a stated mechanical reason rather than a preference: no
truthful `FAIL` reason code exists, and `NO_MEASUREMENT` is the more accurate
terminal anyway (§4.1).

**8.6 Same-instance verification is not attempted.** A-256 stands; §4.5.

**8.7 CIU-21 is unaffected.** The recorded-and-ciu-attested rung of A-255's ladder
still lands with zero assay work when ciu ships it, and is independent of this
item.

**8.8 The JSON-schema tightening of `verified_by_assay` for adjudicated evidence is
deferred** to whichever bump carries `PROVENANCE_UNVERIFIED`; the Python model
tightening lands with W5 (§5.4).

---

## 9. Measurements

All against ciu **6.0.3** (`/home/vscode/.venv/lib/python3.14/site-packages/ciu`),
2026-08-17. The installed package was confirmed **byte-identical** to the monorepo
source used for the source-level readings below:

```
$ diff -q /home/vscode/.venv/lib/python3.14/site-packages/ciu/deploy.py \
          .../assay-B004-provenance/ciu/src/ciu/deploy.py && echo IDENTICAL
IDENTICAL deploy.py
$ diff -q /home/vscode/.venv/lib/python3.14/site-packages/ciu/cli.py \
          .../assay-B004-provenance/ciu/src/ciu/cli.py && echo IDENTICAL
IDENTICAL cli.py
```

**M1 — the brief's JSON sample is incomplete, and exit 0 does not mean verified.**

```
$ cd /tmp/b004-scratch && ciu provenance --json > out.json 2> err.txt; echo "EXIT=$?"
EXIT=0
$ cat out.json
{
  "schema_version": 1,
  "instance": "dstdns-98535c",
  "commit_under_test": "dev",
  "tree_state": "not-a-checkout",
  "containers": null,
  "overall": "not-verified-unknown"
}
$ wc -c < err.txt
0
```

Two findings. (a) The document carries a sixth top-level key, **`overall`**, which
the commissioning brief's sample omitted entirely — and it is the *only* field that
carries ciu's decision. (b) Run from the wrong directory, ciu **exits 0** for a
document that verified nothing. `containers` is JSON `null`, not `[]`.

**M2 — the real document, and every shape a strict parser would have refused.**

```
$ cd /workspaces/dstdns && ciu provenance --json; echo "EXIT=$?"
… 20 containers … "overall": "mismatch"
EXIT=2
$ wc -c   # of the captured document
2377
```

Contradicting the commissioning brief's premise that "on this host every container
is `unlabelled`" and "`labelled_revision` is currently `null`": **four containers
are labelled and report `"status": "mismatch"`**, and `overall` is `"mismatch"`.
Values a plausible invariant would have refused:

| member | real value | the rule it breaks |
|---|---|---|
| `postgres.labelled_revision` | `"refs/heads/master"` | any hex-sha grammar |
| `otel-aggregator.labelled_revision` | `"1400269f8ace841f8d0492f4f9c6c7f305f95268"` (40 hex) | "must equal an 8-char `commit_under_test`" |
| `consul.image` | `"6cf88efc53e8"` | any `name:tag` grammar |
| 16 of 20 `status` | `"unlabelled"` | a two-value `match`/`mismatch` enum |
| `containers` (M1, M8) | `null` | a `list`-typed rule |

**M3 — the mismatches are vendor labels, not dstdns's code.**

```
$ docker image inspect otel/opentelemetry-collector-contrib:latest \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}} | source={{index .Config.Labels "org.opencontainers.image.source"}}'
1400269f8ace841f8d0492f4f9c6c7f305f95268 | source=https://github.com/open-telemetry/opentelemetry-collector-releases
$ docker image inspect timescale/timescaledb-ha:pg18.4-ts2.27.1 \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
refs/heads/master
```

The label points at *open-telemetry's* repository. ciu compares it against dstdns's
short hash (`deploy.py:676`, `actual == commit`), so the mismatch is a false
positive by construction for any third-party image that stamps the standard OCI
label. This is Gate 2.

**M4 — no running dstdns-owned image carries the label, so `verified-match` is
unreachable.**

```
$ for i in $(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^dstdns/'); do
    echo -n "$i "; docker image inspect "$i" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'; done
dstdns/test-runner:latest
dstdns/webapp-server:latest
…
dstdns/admin-debug:latest 4faef35ada7bf95cd577993890f6eca576b83e22
dstdns/ddcli:latest       4faef35ada7bf95cd577993890f6eca576b83e22
```

Every *running* dstdns image is unlabelled. The two that are labelled are not
running — and carry a **40-hex** revision, which ciu's `actual == commit`
comparison against an 8-character short hash would report as `mismatch` even
though they are dstdns's own images.

**M5 — ciu's closed vocabularies, read from the producer.**
`deploy.py:600-690` and its docstring: `overall` is *"one of six closed values"* —
`refused-no-identity`, `not-verified-unknown`, `not-verified-dirty`,
`not-verified-no-evidence`, `mismatch`, `verified-match`. `deploy.py:564`:
per-container `status` is `"match" | "mismatch" | "unlabelled"`. `deploy.py:588`:
`containers: Optional[list[ContainerProvenance]]`, and the `ProvenanceResult`
docstring above it states that `to_dict`'s field order is the wire order.

**M6 — `--ignore-mismatch` exits 0 on a mismatch document.**

```
$ cd /workspaces/dstdns && ciu provenance --json --ignore-mismatch > ign.json 2> ign.err; echo "EXIT=$?"
EXIT=0
$ python3 -c "import json;print(json.load(open('ign.json'))['overall'])"
mismatch
$ wc -c < ign.err
0
```

Third independent demonstration that the exit code is not the verdict.

**M7 — the prose form is not parseable evidence.**

```
$ cd /workspaces/dstdns && ciu provenance; echo "EXIT=$?"
EXIT=2
# stdout: empty
# stderr: "[S17] 4 running container(s) were built from a different commit than the one under test:" + 4 detail lines + remediation prose
```

Nothing on stdout; a human message on stderr. `--json` is the only machine
surface, which is why §4.4 keeps that half of §B004's A-204 citation.

**M8 — the document is non-deterministic run to run, because dstdns has a
concurrent committer.** Three invocations minutes apart in `/workspaces/dstdns`:

```
{"commit_under_test": "682b5b01",       "tree_state": "clean",          "containers": [...20], "overall": "mismatch"}
{"commit_under_test": "c3e17272-dirty", "tree_state": "dirty",          "containers": null,    "overall": "not-verified-dirty"}
{"commit_under_test": "016a2674",       "tree_state": "clean",          "containers": [...20], "overall": "mismatch"}
```

`not-verified-dirty` with `containers: null` occurred **spontaneously**, which
both proves that state is trivially reachable and shows the commit binding will
fire in normal operation (§8.3).

**M9 — the container an assay S3/S4 lane runs inside is unlabelled.**
From M2's document: `{"name": "dstdns-98535c-test-runner", "image":
"dstdns/test-runner:latest", "labelled_revision": null, "status": "unlabelled"}`.
It contributes nothing to the verdict that is meant to describe the run.

**M10 — `commit_under_test` is `git rev-parse --short=8 HEAD`.**
`ciu/src/ciu/engine.py:190-199`: short=8, with a `-dirty` suffix on an unclean
tree, and the literal string `"dev"` when git fails. Note `--short=8` is a
*minimum*, so §3.3's grammar is `^[0-9a-f]{8,40}$`, not "exactly 8" — a rule
demanding exactly 8 would be the same class of false refusal as M2's table.

**M11 — the assay-side constraints, read from the shipped source.**

```
$ PYTHONPATH=src python3 -c "from assay.errors import REASON_CODES; ..."
PASS -> []
FAIL -> ['CANARY_SURVIVED','COMMAND_FAILED','EXCLUDED_LINES','MUTANTS_SURVIVED','UNCLASSIFIED_LINES','UNCOVERED_BRANCHES','UNCOVERED_LINES']
ERROR -> ['BAD_LANE_CONFIG','EXEC_FAILED','FORMAT_MISMATCH','GIT_FAILED','MUTATION_DISCOVERY_FAILED','OUTPUT_WRITE_FAILED','UNREADABLE_ARTIFACT']
NO_MEASUREMENT -> ['BASE_IS_HEAD','BRANCH_UNAVAILABLE','DIRTY_TREE','EMPTY_COVERAGE','HEAD_CHANGED','MISSING_ATTESTATION','MISSING_EXTERNAL_TOOL','STALE_ATTESTATION','TARGET_NOT_MEASURED']
BUDGET_EXCEEDED -> ['LANE_TIMEOUT','MUTANT_LIMIT_EXCEEDED','SNAPSHOT_LIMIT_EXCEEDED']
INCONCLUSIVE -> ['ALL_MUTANTS_EQUIVALENT','CANARY_INCONCLUSIVE','MUTATION_UNSUPPORTED','NO_MUTANTS']
```

- `verdict.py:352` — every non-`PASS` outcome **requires** a reason code; the
  schema's `$defs/status_contract` states the identical rule independently
  (A-182). There is no bare adverse outcome.
- `verdict.py:2084` — `source == "adjudicated"` forbids `producer`,
  `attested_commit` and `reviewed_paths`: **the adjudicated entry has no payload
  slot**.
- `verdict.py:2077-2081` — `MISSING_ATTESTATION`/`STALE_ATTESTATION` require
  `source == "attested"`, so neither is available to B004.
- `config.py:106` — `_EVIDENCE_SOURCES = frozenset({"attested"})`: a lane declaring
  `source = "adjudicated"` is refused at load today.
- `attestation.py:506` — the attested loader refuses a non-attested declaration
  with *"adjudicated evidence has no loader (A-085)"*.
- `verdict.schema.json:505` and `verdict.py:224` — `"adjudicated"` **is** already a
  legal `evidence_source` in the shipped v6 schema and model. A-078's reservation
  holds; no new source value is needed.
- `verdict.schema.json` `$defs/evidence` — `verified_by_assay` is constrained to
  `false` only in the `attested` branch; the `else` branch constrains only the
  attestation payload. §5.4.
- `docs/DESIGN-GUIDE.md:78` — Tier 2 is defined as assay **"invokes** a declared
  third-party tool", which A-030 forbids for ciu and which W5 must amend.
- `tests/test_docs_examples_and_vocabulary.py:287-334` — A-270 check 2 derives four
  vocabularies; `judge.evidence[].source` is not among them (W7).

**M12 — A-O12's `declared_unverified` claim re-confirmed false.**
The string appears nowhere in `src/`, `docs/` or the schema. §B004 recorded this;
this carve re-measured it and W0 corrects the row.
