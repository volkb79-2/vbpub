# B092+B098 final adversarial review handoff

Review the implementation at branch tip `176a06a5e095e92cc94a72fc36658c5e2e343c11`
in `/workspaces/vbpub/.worktrees/assay-b092-b098` before RG-55 merge.

Read first:

- `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`
- repository `AGENTS.md`
- `assay/nyxloom-trove/reports/assay-B092-B098-BRIEF-1.md`
- `assay/nyxloom-trove/4-backlog.md` sections B092 and B098

This is a fresh adversarial review, not an implementation continuation. Check
the complete B092 contract and B098 score documentation against the code,
tests, and shipped loader. In particular, try to falsify these claims:

1. An omitted `identity_exclude` uses the exact pre-B092 whole-tree digest.
2. An explicit native-R2 list filters only matching frozen Git tree leaves;
   it never filters candidates, argv, env, cwd, project prefix, link paths,
   or other identity inputs.
3. Exclusion matching is deterministic, case-sensitive, normalized, and
   refuses malformed/unsafe patterns. Empty declaration and omission have the
   intended distinct identity domains; declaration changes cannot collide.
4. Resume readers and state writers receive the same resolved digest, and the
   filtered implementation does not accidentally use a local filesystem walk.
5. The ingested lane rejects the native-only key; schema version remains 2;
   docs examples load under the shipped grammar and all cross-document links
   resolve.
6. B098 names every excluded canonical bucket, including `crashed`, while
   score arithmetic and verdict semantics remain unchanged.

Use live probes where useful: invoke the real loader on valid/invalid TOML,
exercise `_manifest_sha256` with changed included/excluded entries, and run
the focused test suite. Do not start a mutation campaign: the controller will
schedule assay R2 after review and after a host mutation slot is free. Do not
modify product files or merge. Write a dated review report under
`assay/nyxloom-trove/reports/` with ACCEPT or REJECT, exact commands/results,
and every finding ranked by merge-blocking severity. A fix-verification round
must reuse this same reviewer while alive.
