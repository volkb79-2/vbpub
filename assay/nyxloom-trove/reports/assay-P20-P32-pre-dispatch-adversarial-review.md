# Assay P20–P32 pre-dispatch adversarial specification review

> **Review date:** 2026-08-08; P20/P21 JIT dispositions updated 2026-08-09
> **Roadmap input anchor:** `2f2167f5928e5deacd93f1e9565238aef8acfe32`
> **P20 JIT anchor:** `8aad3dc3b190915bb27881a0f3004b339aeef9c2`
> **P21 JIT anchor:** `618b6f15451ec5f45b5900dc496d794241180467`
> **Authoring doctrine:** `nyxloom/reference/AUTHORING.md` revision
> `2026-08-08-r5`
> **Scope:** every outstanding handoff P20–P32 after the A-167 recarve
> **Method:** the exact `Pre-dispatch adversarial handoff review` prompt in
> AUTHORING: hostile implementer, hostile environment, independent acceptance
> engineer; requirement/oracle traceability; false-PASS attempts; undefined
> grammar/default/ownership/terminal/namespace/bound checks; pairwise and
> combined-axis fixtures; one plausible passing-wrong implementation per oracle.

## Result first

All thirteen frontmatter packages pass `nyxloom lint` (P21 has the intentional
size warning). **P20 is merged; P21 is now READY; P22–P32 are not.** P21's
carver-owned proof freeze is complete at the P20 merge anchor; the exact rerun
and six-part disposition live in `assay-P21-JIT-CARVE.md`. The successors remain provisional until their
predecessor merges. Lint proves machine shape only; it does not create the
skeletons, goldens, pinned external inputs, or controlled failing negatives
that AUTHORING requires.

This audit was not ceremonial. It found and corrected seven contract defects in
the recarve before reaching the dispositions below:

1. v4 mutant identities still collided for same-line/same-description sites;
   P21 now carries UTF-8 byte span plus replacement hash.
2. v4 had no truthful terminal for a failed syntax/helper discovery boundary;
   P21 now reserves `ERROR/MUTATION_DISCOVERY_FAILED` and distinguishes a valid
   zero-site result.
3. P21/P23's `max_mutants` was a false bound because the old adapter constructed
   the full tuple before the caller counted it; the P21 JIT pass moved bounded
   common/Python `MutationSite` discovery into P21 itself so the v4 cap is true
   when introduced (A-180). P23 consumes it.
4. the old Go packet's legal `64 MiB × 10,001 mutated_text` shape implied
   hundreds of GiB; P29's wire contains small site descriptors only.
5. attestation allowed symbolic commit identity and a path-check/open race; P26
   now requires a full lowercase OID and descriptor-relative no-follow traversal.
6. P27 simultaneously required inclusive Go block expansion and named it as a
   false attribution; it now requires a real half-open position probe and hand
   manifest before freezing the grammar.
7. P31's “not imported” wrong-cause fixture was unreachable under `go test
   ./...`, which still runs that package; it now uses a separately declared
   package-scoped plan while R1 measures both packages.

## 1. Blocking ambiguities and readiness dependencies

| package | remaining blocker before ACTIVE | authority that resolves it |
|---|---|---|
| P20 | **closed:** compiling safe-I/O skeleton, locked acceptance, handwritten artifact, hostile Git tracer, `C.UTF-8`, explicit Git-dir/work-tree anchoring and real-gate witness are committed | `assay-P20-JIT-CARVE.md` / A-173–A-176 |
| P21 | P20's terminal mapping/API is now frozen but not landed; the packet intentionally abbreviates unchanged v4 fields and has no complete valid/invalid model/schema/raw-verifier goldens yet | Sol xhigh after P20 merge; Opus implementation |
| P22 | P20 Git is frozen but not landed and P21 terminal signatures are not landed; private-object-pack, malformed-tree, symlink and limit skeleton/assets are absent | Sol xhigh after P21; Opus implementation |
| P23 | P22's real public signatures are unknown; Python site-parity manifests, process ledger and injected-budget fixtures are absent | post-P22 JIT freeze; Sonnet implementation |
| P24 | no positive offline wheelhouse/build manifest or actual positive release-manifest golden is committed | post-P23 pre-dispatch freeze |
| P25 | exact Topos/vbpub commit, literal patch, manifest, expected v4 artifact, wheel hash and independent command are not pinned | post-P24 pre-dispatch freeze |
| P26 | P21's final evidence grammar is not landed; full-OID, dirfd/symlink-swap, path-byte and bound fixtures are absent | post-P25 pre-dispatch freeze |
| P27 | image/toolchain digest and lock are not frozen; the half-open Go block-to-line rule is intentionally awaiting a real profile/source/manifest probe | post-P26 pre-dispatch freeze; Sol on contradiction |
| P28 | base/child OIDs, exact srdm replacement, image ID, wheel hash, commands, line manifest and expected artifact are deliberately unbound | post-P27 pre-dispatch freeze |
| P29 | P23's landed site API is unknown; compiling helper/adapter skeleton, protocol goldens and bounded-memory attack are absent | Sol xhigh after P28; Opus implementation |
| P30 | helper hash/path, tiny outcomes, real srdm site/outcome, v4 artifacts and budget ledger are absent | post-P29 pre-dispatch freeze |
| P31 | exact tiny/srdm targets, package-scoped wrong-cause lane and complete artifacts are absent | post-P30 pre-dispatch freeze |
| P32 | the pinned image/digest and npm integrity claims have not been re-probed; lock, real reports, source manifest and parser goldens are absent | post-P31 pre-dispatch freeze |

There is no remaining permission to let an implementer pick these values. Each
handoff names the missing proof and returns NOT READY until it exists.

## 2. False-PASS attacks

| package | plausible wrong implementation that can pass convenient tests | required combined attack |
|---|---|---|
| P20 | sanitizes `GIT_DIR` but inherits config/object variables; checks artifact path before open; checks dirt only under source roots | two repositories + hostile config + renamed artifact parent + stale hardlink + support-file mutation in one run |
| P21 | model/schema/verifier share generated examples; identity sort uses line/description; helper failure becomes no-mutants | hand-edit complete JSON independently: collide two same-line sites, mutate replacement hash, delete required payload, and fail discovery with zero submissions |
| P22 | uses source object alternates or `git archive`; bounds compressed pack only; yields a partial tree before rejecting a late entry | hostile replace/config + nested sibling + limit+1 uncompressed closure + escaping symlink/malformed mode; prove no source-store path/write and no yielded partial snapshot |
| P23 | resolves argv/env again in R2/R3; applies max after adapter tuple; copies baseline profile; resets budget per process | nested project + appended argv + passthrough + multi-file max+1 + stale profile + injected deadline in one process-ledger fixture |
| P24 | asserts only metadata version; imports source via `PYTHONPATH`; generates expected manifest from bytes under test; dirty build preserves clean identity | two independent tagged builds + source-path poison + dirty source + wrong hash/version/filename changed separately, all verified before install |
| P25 | uses a hello-world Topos-shaped fixture; lets Assay generate its expected artifact; compares exclusions with a tool that cannot express them | real pinned Topos tree + hand manifest + unmodified copied comparator, with repo/project split, excluded-forbidden asymmetry and command-created support mutation |
| P26 | resolves `HEAD` at verification time; `resolve()` then opens path; splits Git display names; exact-membership treats reviewed directory as file | full old OID + directory descendant change + newline/pathspec-magic name + parent/symlink swap + oversize sibling, with later identity still processed |
| P27 | hardcodes module prefix; trusts ambient Go; uses inclusive block lines; uses committed profile | renamed deep module + nested project + real generated profile ending at column 1 + ambient Go poison + normalized-key collision |
| P28 | Assay and covergate construct each other's expectation; extracts module to repo root; compares only final exit | preserved real prefix + multi-package patch + three independent line sets + exclusion asymmetry + shared checkout hash/status |
| P29 | regexes source; uses character offsets; emits full mutated files; treats error frame as empty success; collects all then truncates | Unicode-before-token + same-line sites + max+1 + malformed/oversize response in one fixture; assert response bytes and unchanged P21 Python manifests |
| P30 | submits before knowing max+1; parses `go test` text; shares snapshot/cache; forces a fake crashed result | reordered concurrent completion + appended argv/env + max+1 + compile rejection + valid zero-site + helper failure, asserting boundary-only buckets and zero source writes |
| P31 | accepts any transformed non-PASS; reuses control profile; “unused” package still runs under `./...`; omits R1 for uncovered-line | positive cause pairs plus package-scoped unused-target wrong-cause lane, no-output transform, malformed/no-op and broken control with exact fresh-profile ledger |
| P32 | commits generated reports; last record wins; any-hit wins same-line Istanbul; reports unavailable exclusions as empty | same real run emits both formats; reverse/split repeated lcov records; mixed Istanbul counts; ignored/type-only/multiline/JSX plus stale report and source manifest |

## 3. Missing implementation-packet material

P21 now has its complete proof packet. The following successor material is
deliberately not yet present and therefore prevents P22–P32 from being READY:

- P22/P29 lack the mandatory compiling skeletons and witnessed failing
  acceptance negatives assigned to Sol. P20/P21 are frozen under their
  respective `nyxloom-trove/carve-assets/` directories.
- P23 lacks a process-ledger spy at P22's eventual landed signatures. P21 now
  owns and locks the Python `MutationSite` parity corpus.
- P24 lacks the positive wheelhouse and release-manifest artifact.
- P25/P28 lack exact external commit/patch/command/image/wheel manifests.
- P26 lacks descriptor-race and full-OID goldens.
- P27 lacks the real position-to-line golden that decides the parser grammar.
- P30/P31 lack exact real Go/srdm outcome artifacts.
- P32 lacks the npm lock, generated-report goldens, and independent source
  statement manifest.

An implementation-authored substitute does not satisfy this list. It would make
specification, implementation, and acceptance share the same assumption again.

## 4. Scope and dependency defects

The frontmatter chain is now a valid strict serial P19→P32 graph and all files
lint clean. The audit corrected these scope/dependency issues:

- P22 is substrate-only; P23 owns runner/mutation/canary integration.
- P21, not P23/P29, owns the common Python/core site-contract conversion,
  because its own v4 cap must be real when introduced. P23 consumes it and P29
  implements it for Go.
- P27 is tiny real Go adapter/gate only; P28 owns external srdm R1.
- P29 is helper/Go-adapter discovery only; P30 owns R2 execution/capability.
- P30 records the real crashed-bucket reachability limit instead of demanding an
  impossible process-boundary failure under an identical baseline plan.
- P28's frontmatter forbids only project-resolving paths because Nyxloom lint
  rejects sibling-project pseudo-paths; its normative body still expressly
  forbids srdm/shared-image edits. JIT review must add any newly existing P22/P29
  paths to frontmatter forbid once they resolve.

One tradeoff is explicit: strict serial dependencies delay P32 even though its
code may need only v4. This is a cost/control choice for the semi-manual wave,
not a technical dependency claim. It preserves one merge/cache stream and lets
the controller stop after any qualification failure.

## 5. Corrected oracle/fixture matrix

These are the minimum pairwise/combined fixtures the JIT carver must instantiate;
the detailed rows live in each handoff.

| seam | axes that must occur together | independent observable |
|---|---|---|
| repository/artifact | hostile Git env × repo≠project × artifact-parent rename × post-command support mutation | exact Git/process ledger, descriptor inode, complete terminal, source status |
| v4 | same-line sites × killed identity × operator policy × max sentinel × old version | three independent validators over handwritten whole documents |
| snapshot | nested project × tracked sibling × hostile replace/filter × symlink × object/pack limit | literal tree/object manifest, private repo OID, source-object-store hash |
| repeated execution | appended argv × passthrough × nested cwd × stale profile × shared deadline | byte-equal process ledgers and handwritten artifacts |
| wheel/Python consumer | tagged wheel/hash × source-path poison × real Topos delta × exclusion asymmetry | metadata/import origin, hand manifest, unmodified Topos result |
| attestation | full old OID × reviewed directory × newline/pathspec-magic child × dirfd swap × later sibling identity | bounded Git argv/exit ledger and exact evidence array |
| Go R1 | renamed module × nested prefix × end-column-1 block × comment/doc/test-only file × real profile | Go-produced profile, hand statement manifest, exact artifact |
| srdm R1 | real multi-package tree × controlled child commit × shared semantic intersection × unavailable exclusion | Assay, copied covergate, hand manifest, shared-tree hash |
| Go discovery | Unicode × same-line operators × max+1 × malformed/oversize protocol | exact site/wire goldens, parser validity, bounded descriptor count/memory |
| Go R2 | selected operator × max+1 × reordered jobs × compile nonzero × valid empty × helper error | process boundary ledger and complete v4 identity buckets |
| Go R3 | positive control × fresh profile × exact cause × package-scoped wrong cause × no-op | per-half ledgers/profiles and exact canary artifact |
| Vitest | real dual-format run × same-line mixed statements × repeated lcov records × ignored/type-only/multiline/JSX | literal source manifest and order-invariant parsed profiles |

## 6. Disposition

| package | disposition | why |
|---|---|---|
| P20 | **MERGED** | landed as `618b6f15` after locked acceptance and controller-owned gate |
| P21 | **READY — IMPLEMENT NEXT** | exact JIT review at `618b6f15`; full migration/site/output goldens, skeleton, and 24 controlled reds committed |
| P22 | **NOT READY — PROVISIONAL** | depends on P21/P20 landed boundaries; security fixtures absent |
| P23 | **NOT READY — PROVISIONAL** | depends on P22 signatures; process-ledger/snapshot integration assets absent; P21 already owns site parity |
| P24 | **NOT READY — PROVISIONAL** | positive offline build/release inputs absent |
| P25 | **NOT READY — PROVISIONAL** | external commit/patch/manifest not pinned |
| P26 | **NOT READY — PROVISIONAL** | final v4 evidence shape and hostile safe-I/O fixtures absent |
| P27 | **NOT READY — PROVISIONAL** | toolchain/image and block grammar not probed/frozen |
| P28 | **NOT READY — PROVISIONAL** | real srdm case/witnesses not pinned |
| P29 | **NOT READY — PROVISIONAL/JIT FREEZE** | depends on P23 API; helper skeleton/protocol assets absent |
| P30 | **NOT READY — PROVISIONAL** | real tiny/srdm outcomes and helper identity absent |
| P31 | **NOT READY — PROVISIONAL** | cause-sensitive real fixtures/artifacts absent |
| P32 | **NOT READY — PROVISIONAL** | pinned producer closure and parser/source goldens absent |

The intended next action is not to recarve all thirteen again. Implement,
independently review, and merge READY P20, then update only the successor
horizon affected by what actually landed and JIT-freeze P21.
