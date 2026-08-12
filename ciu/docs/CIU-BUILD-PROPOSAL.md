# CIU build proposal — superseded

This historical proposal described a separate `ciu-build` tool and retired
`release.toml` / `build-push.toml` configuration. Neither is supported.

CIU is released through its own [`cmru.toml`](../cmru.toml), selected by the root
[`cmru.orchestration.toml`](../../cmru.orchestration.toml). Its isolated release gate is
the project-local `tester-gate` command; CMRU provides the transaction, stable logs, release
history, `--show-run-details`, and `--log-append` behavior. See the
[CMRU README](../../cmru/README.md) and [spec](../../cmru/docs/SPEC.md).
