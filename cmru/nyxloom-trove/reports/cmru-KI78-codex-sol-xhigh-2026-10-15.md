# CMRU KI-78 independent review

- Reviewer: GPT-6-Sol, xhigh
- Context: `CODEX_HOME=$HOME/.codex`, shared SQLite session database
- Reviewed commits: `44fde767f` through `4fbf4beb8`
- Scope: OCI image-only GitHub Release body handler, configuration validation and dry-run, tests and adopter docs, and CGProfile handler registration
- Review type: static adversarial review; no tests or gates run
- Result: six actionable findings; implementation is not ready to merge until resolved and re-reviewed

## Findings

1. **P1 — A successful write can be reported as published while the intended body is no longer present.** `cmru/src/cmru/release.py` skips the Release read-back after create. After update, the read-back checks ID and tag but not body. A concurrent replacement after POST, or body overwrite after PATCH, can leave the wrong body visible while the handler returns success and CMRU promotes the candidate. Add oracles that replace the Release after POST while keeping the Git tag unchanged, and that alter only the body on the post-PATCH Release GET.

2. **P2 — The pre-PATCH recheck omits an existing Release’s commit target.** The initial lookup compares a full `target_commitish` SHA with the protected commit, but `_verified_existing_release` does not repeat that comparison on its immediate pre-PATCH fetch. If the field changes to another full commit SHA between reads, the handler overwrites its body. Add an oracle that keeps ID and tag fixed while changing `target_commitish` before the PATCH; assert no PATCH is sent.

3. **P2 — A post-write transport failure loses the “was written” diagnostic.** `_verify_after_write` catches `SystemExit` only. A `URLError` or malformed JSON during read-back escapes without warning that the write already happened. Add an oracle that raises `URLError` on the read-back GET after a successful PATCH and require an explicit “was written / not rolled back” message.

4. **P2 — Dry-run can execute `prepare` before rejecting invalid handler configuration.** `_validate_release_body_handlers` runs after `_prepare_dry_run_external_versions`. For an `external:` version project, `cmru release --dry-run` can execute its prepare step before discovering a malformed `oci-image-release` command. Add a marker-writing prepare fixture with an invalid image handler and assert the marker is absent.

5. **P2 — Image validation accepts a path Docker interprets as Docker Hub.** `validate_oci_image_repository` accepts `registry/team` as `<registry>/<repository>`, but Docker interprets a first component without `.` or `:` (except `localhost`) as a Docker Hub namespace. The generated pull instruction can target a different registry than intended. Reject ambiguous first components or explicitly define the accepted registry grammar; add an oracle for `registry/team`.

6. **P3 — CGProfile’s public release body is absent from adopter docs.** The new handler registration makes CGProfile publish tagged notes, but the CGProfile README, design guide, and consumer guide still describe image publication only. Document the body, its position after the image push, and the `cmru release --dry-run` preview across those documents, with the corresponding doc oracles.

## Required disposition

Resolve all six findings with behavioral tests and synchronized CMRU/CGProfile docs. Then rerun the registered gates and obtain a fresh independent review on the final exact candidate.
