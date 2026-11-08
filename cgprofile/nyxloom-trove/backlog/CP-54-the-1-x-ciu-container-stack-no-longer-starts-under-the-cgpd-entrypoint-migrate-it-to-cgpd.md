---
kind: backlog-entry
schema_version: 1
id: CP-54
title: "The 1.x ciu container stack no longer starts under the cgpd entrypoint: migrate it to cgpd (cli-extended in the image, a mounted cgpd.toml, no old env vars or flags)"
status: open
type: "bugfix"
severity: "medium"
component: "deployment"
priority: 2
provenance: "cgp2 package 3, 2026-11-04: Dockerfile entrypoint moved to `python3 -m cgprofile.cli.cgpd` with `CMD [\"run\"]` (3f0e314b4)"
filed_by: "cgp2-pkg3"
filed_date: "2026-11-04"
---

## Problem

Package 3 retired the `serve` flags and `CGPROFILE_ALLOW_UIDS` / `CGPROFILE_PID_NAMESPACE_MODE` and moved the image entrypoint to `cgpd run`.
`ciu.compose.yml.j2` and `ciu.defaults.toml.j2` still pass the old environment variables and `--sessions` flags, so the 1.x container stack is
refused at start (stale-env refusal, unknown flags). The image also lacks cli-extended and any `cgpd.toml`.

`tests/test_deployment_contract.py` still pins the old compose shape.

## Expected

The container stack runs `cgpd run` with cli-extended installed in the image, a mounted `cgpd.toml` carrying the access list and paths, and none of
the old variables or flags. The deployment contract test pins the new shape. This may be superseded by the native deployment (docs/NATIVE-DEPLOYMENT.md);
decide between migrating and retiring the container stack.

## Oracles

`ciu up` of the stack starts a daemon that answers `version` at contract 2; `test_deployment_contract` fails if any retired variable or flag returns.

## Resolution note (2026-11-04, controller decision)

Retire, not migrate: cgprofile 2.0.0 ships the wheel only. `cmru.toml` no longer builds or pushes the image (`artifacts = ["wheel"]`), so
the stale stack is not released. The Dockerfile, compose and defaults templates stay in the tree for plan package 9 (CP-44) to delete.
Status stays open until that deletion lands.
