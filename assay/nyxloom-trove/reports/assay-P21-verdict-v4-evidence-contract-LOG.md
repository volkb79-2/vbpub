# assay-P21 — implementation LOG

> **Result: BLOCKED.** No product code was written. The branch contains this
> LOG and nothing else.
>
> **Implementer:** fresh Opus xhigh child, forked from the package-neutral
> frozen orientation base at `20beeda152a08114692fb846aee6dc0118f1e86a`.
> **Worktree:** `/workspaces/vbpub/.worktrees/assay-P21-verdict-v4-evidence-contract`
> **Branch:** `feat/assay-P21-verdict-v4-evidence-contract`
> **Date:** 2026-08-09

## BLOCKED

`BLOCKED: the frozen packet's mandatory deletion of the old full-text mutation
surface cannot be performed without editing src/assay/adapters/go.py, which is
in scope.forbid — and the replacement protocol supplies no successor for the
"UNSUPPORTED" sentinel that is the Go adapter's only truthful mutation
terminal.`

Two coupled defects, both reproduced against the repository rather than
inferred. They share one amendment, so they are reported together.

### 1. The deletion requires a forbidden file

The implementation packet states, under *Owned interfaces* and again in work
item 4:

> The old `Mutant`, `generate_mutants`, `collect_mutants`, and full-text
> identity are **deleted, not retained as compatibility surfaces**. Go R2 is
> not registered; `go.py` remains untouched until P29 implements this
> already-landed protocol.

`go.py` cannot remain untouched across that deletion. It holds an
unconditional module-level import of the deleted name:

- `src/assay/adapters/go.py:159` — `from ..mutation import Mutant`
- `src/assay/adapters/go.py:518-520` — `def generate_mutants(...) -> tuple[Mutant, ...] | Literal["UNSUPPORTED"]`

`from __future__ import annotations` (line 154) defers the *annotation* only;
the `import` statement still executes at module load.

**Reproduction** (disposable copy of `src/`, real tree never modified; the copy
was deleted afterwards). The only edit was renaming the class exactly as work
item 4 instructs:

```text
$ PYTHONPATH=<copy>/src python -c "import assay.adapters.go"
  File "<copy>/src/assay/adapters/go.py", line 159, in <module>
    from ..mutation import Mutant
ImportError: cannot import name 'Mutant' from 'assay.mutation'

$ PYTHONPATH=assay/src python -c "import assay.adapters.go; print('control import OK')"
control import OK
```

Blast radius: `assay.adapters.go` becomes unimportable, so the **12 test
modules that import `GoAdapter`** fail at collection. No *production* module
imports `adapters.go` (verified: `registry.py`, `canary.py`, `cli.py`,
`adapters/base.py` mention it only in prose), so this is a suite-wide
collection break rather than a runtime product break — but it is a break, and
it lands in a file this package may not touch.

`adapters/base.py:41` and `adapters/python.py:163` hold the same import and are
both in `scope.touch`, so they are not part of this block. **`go.py` is the
only forbidden file involved.**

### 2. The new protocol has no successor for `UNSUPPORTED`

This is why the block is not merely "widen `scope.touch` by one file".

The packet's replacement signature returns a plain tuple:

```python
def generate_mutation_sites(
    self, text: str, lines: set[int], *,
    operators: tuple[str, ...], limit: int,
) -> tuple[MutationSite, ...]: ...
```

and fixes exactly two terminals for it — *"Invalid Python syntax raises
`MutationDiscoveryError`; valid syntax with zero sites returns `()`"*, with
A-171 binding `()` to `INCONCLUSIVE/NO_MUTANTS` and `MutationDiscoveryError` to
`ERROR/MUTATION_DISCOVERY_FAILED`.

The `Literal["UNSUPPORTED"]` member of the old union is deleted with it. That
sentinel is the **only** vocabulary `GoAdapter` has for *"this adapter has no
mutation engine at all"*, and it is load-bearing prior product truth:

- **A-042 / A-087** — no Go toolchain exists anywhere in this devcontainer, so
  no generated Go mutant can be proven valid; `GoAdapter.generate_mutants`
  returns `UNSUPPORTED` unconditionally.
- **A-114** — `UNSUPPORTED` is pinned as a whole-adapter-call union member,
  explicitly *"never a per-construct union"*.
- **A-011** — `UNSUPPORTED` renders `INCONCLUSIVE_NO_MUTANTS`, **never green**.

Neither surviving terminal is truthful for Go. Returning `()` asserts *"this
file was validly analysed and has nothing mutable"* — a positive measurement
claim about Go source that no Go analysis was performed to support, which is
the lane-table-implies-capability failure this project exists to remove.
Raising `MutationDiscoveryError` asserts a discovery **boundary failure**
(`ERROR`), which is equally false: nothing failed, the capability was never
built.

So even with `go.py` added to `scope.touch`, what `GoAdapter` exposes after
this migration is an **unspecified, externally visible decision**. Contract
class 2b assigns that to the carver — *"Implementer freedom: private
construction only. Names, types, requiredness, operator/reason vocabularies,
migration behavior, and cross-field invariants are fixed"* — and AUTHORING's
own pre-dispatch review requires NOT READY when *"any externally visible
decision, interface, example, bound, refusal, or proof source remains for the
implementer to invent"*. I am not authorised to invent it, and inventing it
would silently retire A-042/A-011's honest terminal.

## Why no partial implementation was landed

Work item 1 requires the v4 bump *"in one atomic migration"*, and A-180 is
explicit that the bounded site seam must land **with** the cap it makes true:

> Deferring the repair to P23 would ship two packages with a knowingly false
> v4 cap.

Landing work items 1-3 and 5-10 while leaving the adapter seam on the old
full-text surface would ship exactly that false cap — `Mutation.candidate_count`
and `judgment.r2.max_mutants` advertising a bound no discovery path enforces.
That is the specific defect this JIT carve was performed to prevent, so a
partial landing is worse than none. Per the BLOCKED rule and canonical
DOCTRINE §3, nothing was improvised.

## What was done before stopping

State reconciliation (all mechanical, all passed):

```text
git rev-parse HEAD                    20beeda152a08114692fb846aee6dc0118f1e86a
merge-base --is-ancestor anchor HEAD  yes (anchor == HEAD)
branch                                feat/assay-P21-verdict-v4-evidence-contract
git status --porcelain                clean (0 lines)
git diff --name-status <anchor>..HEAD (empty, as the dispatch predicted)
git diff <anchor> -- assay/src assay/tests assay/assay.toml assay/pyproject.toml
                                      (empty)
```

Locked skeleton applied and the locked acceptance run once, per the dispatch
recipe, to witness the intended controlled red:

```text
$ git apply assay/nyxloom-trove/carve-assets/P21/skeleton.patch
APPLY=ok
$ python -m py_compile assay/src/assay/output.py
COMPILE=ok
$ PYTHONPATH=assay/src python -m pytest --override-ini=pythonpath= \
    assay/nyxloom-trove/carve-assets/P21/test_acceptance.py -q
24 failed in 0.80s
```

**24 failed, zero collection/setup errors — exactly the count and shape
`reports/assay-P21-JIT-CARVE.md` witnessed (`24 failed in 0.85s`).** The locked
packet's own baseline reproduces faithfully at this anchor; the block is in the
packet's mutation-seam contract, not in its proof assets.

The applied skeleton was then **removed**, so the branch carries no
half-implemented `output.py`. The controller reapplies it on redispatch. Every
carve-asset under `nyxloom-trove/carve-assets/P21/` is byte-for-byte unchanged
(verified against the eight SHA-256 hashes recorded in the JIT-CARVE report).

## Amendment needed to unblock

One bounded carver decision, then redispatch. Both parts are needed; part (b)
alone leaves the import broken, part (a) alone leaves Go's terminal invented.

**(a)** Add `src/assay/adapters/go.py` to `scope.touch` (it stays out of
`scope.forbid`), limited to the two lines the deletion forces: the
`..mutation` import and the mutation method's signature.

**(b)** Rule what `GoAdapter` exposes for mutation between P21 and P29, as a
`D-`/`A-` decision, not an implementer choice. The three candidates I can see,
with what each costs:

1. **Retain a capability terminal on the new seam** — e.g. a
   `MutationUnsupported` marker or an `unsupported: bool` on the adapter,
   distinct from both `()` and `MutationDiscoveryError`, preserving
   A-011/A-114's honest `INCONCLUSIVE/NO_MUTANTS`. Keeps prior truth; costs one
   more shape in the frozen v4 seam.
2. **Drop the mutation method from `GoAdapter` entirely** — Go is unregistered
   for R2, so nothing calls it; the capability question moves to registry
   preflight, where A-013/A-142/A-144 already put "prerequisite absent". Costs
   a `LanguageAdapter` protocol that `GoAdapter` no longer structurally
   satisfies in full.
3. **Declare Go's mutation prerequisite missing** — render
   `NO_MEASUREMENT/MISSING_EXTERNAL_TOOL`, which P21 already reserves for P27.
   Cleanest semantically; but P21 reserves that reason explicitly *for P27's
   first real external-tool preflight*, so using it here changes what P21's own
   reservation means.

I have deliberately **not** chosen between these. Each is externally visible
and each rewrites an already-decided terminal.

Everything else in the packet was read and is implementable as specified: the
three complete v4 documents, the fourteen invalid cases with their exact
per-layer ownership, the UTF-8 site manifest (offsets independently
recomputed against the declared text and confirmed correct — `<` at bytes
36/40, `and` at 44-47, `True` at 48-52, with the two-byte `π` prefix accounted
for), the max+1 zero-submission sentinel, the output reservation state machine,
and the `HEAD_CHANGED`/`DIRTY_TREE` precedence repair.
