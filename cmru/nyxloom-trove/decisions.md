# CMRU decisions

Product decisions for CMRU, numbered `D-<NNN>`. Each records what was decided, who decided it,
why, and what it binds. The user-facing rationale lives in `docs/DESIGN-GUIDE.md` and links back
here.

## D-001 — A release retry keeps its exact version plan

- **Date / decided by:** 2026-10-18, the release controller, resolving the BLOCKED version-intent
  finding of `reports/cmru-6.2.0-independent-final-review-claude-opus-20261018.md`.
- **Decision:** A release retry must preserve the exact version plan chosen by the original
  invocation.
  - Record the resolved per-project version plan durably before any release side effect.
  - A resume with no version override reuses that recorded plan.
  - A caller-supplied conflicting override refuses before the candidate, a tag, or publication
    state changes.
  - A legacy transaction without trustworthy version intent may resume only if its exact plan
    can be proven from committed candidate evidence; otherwise it refuses before side effects.
  - CMRU never silently recomputes a later version.
- **Why:** Preserving the operator's release intent is the safe behavior for a resumable
  transaction. Recomputation from whatever flags a retry carries shipped wrong versions, and it
  stranded curated notes under a never-released section.
- **Binds this release:** a transaction started with `cmru release cmru --minor --from-worktree`
  resumes as exactly CMRU 6.2.0 even when `--minor` is omitted on the resume command.
- **Implementation:**
  - The plan is recorded in `<token>.version-plan.json` in the shared transaction metadata,
    bound to the transaction branch and base commit. `null` marks an external-version or
    no-tag project.
  - The plan is written once by the release child, after planning and before the scope record
    and first candidate push. It is validated on every read.
  - An override selecting the same version is accepted.
  - The legacy proof is exactly one new dated changelog section since the transaction base.
  - Rationale: `docs/DESIGN-GUIDE.md` § "A retry keeps its recorded version plan (D-001)".
  - Normative text: `docs/SPEC.md`, release-transaction version intent.
