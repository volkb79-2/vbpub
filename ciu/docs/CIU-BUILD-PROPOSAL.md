# CIU build proposal — superseded

This historical proposal described a separate `ciu-build` tool and retired
`release.toml` / `build-push.toml` configuration. Neither is supported.

CIU is released through the root [`cmru.toml`](../../cmru.toml) declaration and
the CMRU wheel profile. Its isolated release gate is the `project.ciu` entry’s
`tester-gate` command; CMRU provides the build, publish, stable logs, release
history, `--show-run-details`, and `--log-append` behavior. See the
[CMRU README](../../cmru/README.md) and [spec](../../cmru/docs/SPEC.md).
