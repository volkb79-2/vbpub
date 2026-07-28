# Isolated release transactions

`cmru release` is intentionally safe to start from a busy developer checkout.
It does not build, tag, or publish from that checkout. Instead it takes a
committed snapshot of `origin/main` and performs all release work in a private
`cmru/release/<id>` worktree.

## Operator contract

Use the ordinary entry point:

```bash
./cmru.release.sh --project <project>
```

cmru obtains a local release lock, fetches `origin/main`, and compares it with
local `main` before creating the release worktree. Local-only `main` commits are
a fail-closed error: the snapshot would omit them, so push them first. A local
`main` that is behind is reported but safe—the fetched remote commit is
authoritative. cmru then creates the release worktree and streams the child
process output back to your terminal. It copies
the optional `cmru.secret.toml` overlay into that worktree with mode `0600`; the
copy is removed with a successful worktree and is never staged.

Uncommitted edits outside the selected project do not block a release. Edits to
the selected project's declared version paths, or to `cmru.py`, `cmru/`, or
`cmru.toml`, do block it: otherwise the snapshot would silently release older
inputs than the operator sees. This distinction is necessary because
setuptools-scm treats *any* dirty file in its worktree as a dirty build.

Failure retains the worktree and prints its path and branch. Inspect and commit
the correction there, then resume it explicitly:

```bash
./cmru.release.sh --resume /path/reported/by/cmru --project <project>
```

Do not copy generated files back into the caller's dirty checkout. A successful
transaction removes the ephemeral branch/worktree.

## Transaction order

```text
caller checkout
    │  lock + fetch origin/main + reject local-only main commits + validate release inputs
    ▼
cmru/release/<id> at immutable origin/main commit
    │  optional prepare → commit declared mechanical outputs
    │  required tester-unified gate
    │  fast-forward origin/main (or fail on concurrent remote update)
    │  explicit tag → build → publish → validate
    ▼
immutable public artifact linked to its source commit
```

There is no in-place release mode. A local lock prevents two releases on one
clone; the fast-forward push protects the final source integration from other
clones. Publication cannot begin after either failure.

## Project author contract

Every releasable project must declare a meaningful `steps.run-tests` command.
It must invoke the project’s real gate in `tester-unified`, not the developer
container. cmru refuses to tag or publish a changed project with no such step.

Use `steps.prepare` only for mechanical, deterministic release input changes.
List every tracked output in `release.commit_generated`; cmru rejects an
undeclared write. A prepare step that derives a version writes it to
`<project>/cmru.vars`, and the project declares `version.strategy =
"external:VAR"`. cmru then creates the annotated tag after the prepared source
is gated and integrated. Projects do not create release tags through a build
script or an implicit GitHub Release API side effect.

OCI projects must not push while gathering generated provenance. Build privately
first, commit/promote declared provenance, then run the separate registry push.

## vbpub gate-audit follow-up

The transaction engine now fails closed, exposing existing configuration debt:

| Project | Current state | Required follow-up |
|---|---|---|
| ciu | Has `run-tests`, but it uses the cockpit interpreter. | Wrap its coverage command in the mounted `tester-unified` gate. |
| cmru | No release `run-tests` step. | Add tester-unified pytest gate for `cmru/tests`. |
| nyxloom | Has a real gate in its nyxloom trove, but cmru does not declare it. | Add that tester-unified coverage gate as cmru's release step. |
| MDT | Has focused release-flow tests, not a declared release gate. | Define its tester-unified suite and keep private-build-before-push ordering. |
| pwmcp | No declared release gate. | Create a deterministic tester-unified gate for resolver/build-contract behavior. |
| tls-edge | No declared release gate. | Define the tarball/installer gate before enabling automated releases. |

Until these commands are supplied, a changed project fails before remote main,
tags, or public artifacts change. That is intentional: a missing gate is not a
passing gate.
