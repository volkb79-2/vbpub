---
kind: backlog-entry
schema_version: 1
id: NL-39
title: "nyxloomd image should install the released cli-extended wheel (sha256-verified), not a source build with a pretend version"
status: open
type: "feature"
severity: "medium"
component: "packaging"
provenance: "MM-MOVE review follow-up 2026-10-07 (controller ruling; CX-D2)"
filed_by: "Claude Sonnet (MM-MOVE implementer)"
filed_date: "2026-10-07"
---

## Observed

NYX-CLIX (branch `mm-move`, 2026-10-07) made nyxloom depend on the `cli-extended` wheel instead of vendoring it. To keep the nyxloomd image buildable, `nyxloom/nyxloomd/Dockerfile` now BUILDS a cli-extended wheel from the monorepo source (`libraries/cli-extended/{pyproject.toml,README.md,src}`) with `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED=${CLI_EXTENDED_VERSION}` (default `0.3.0`) and installs it first with `--no-index --no-deps`. The reviewer of the package flagged this: the image then carries a source-built wheel stamped with an invented version, not the released artifact.

## Expected (controller ruling 2026-10-07)

The nyxloomd image installs the RELEASED cli-extended wheel, verified by sha256, exactly as the `tester-unified` image does (`tester-unified/fetch-cli-extended.py`, release pointer `cli-extended-latest/latest.json`, `libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md` CX-D2: release assets from GitHub Releases, `--no-index`, never PyPI). No pretend version, no `libraries/cli-extended` COPY, and the root `.dockerignore` whitelist for `libraries/cli-extended/{pyproject.toml,README.md,src}` (added by NYX-CLIX) is removed again.

## Oracles

- `docker history`/build log of the nyxloomd image shows the cli-extended wheel installed from a downloaded release asset whose sha256 matches the pointer; a wrong digest fails the build.
- The Dockerfile contains no `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED` and no `COPY libraries/`.
- `/opt/nyxloom-venv/bin/pip show cli-extended` reports the released version.

## Related

CX-D2 (cli-extended unified-adoption program); NL-30 (nyxloom adoption, packaging items done by NYX-CLIX); `nyxloom/nyxloom-trove/reports/MM-MOVE-REPORT.md`.
