# Licensing

Status: PROPOSED (operator has not yet confirmed MIT; see the decision list in the audit report
`vbpub-release-program-20261027/reports/LICENCE-AUDIT-2026-11-05.md`).

## Rule

First-party code in this repository is `MIT` (root `LICENSE`, holder from the project `authors` entries).
Third-party and vendored components keep their own licences and are never relabelled MIT.

## Python projects (wheels on GitHub Releases)

Each project below declares `license = "MIT"` in its `pyproject.toml` and carries a `LICENSE` file that is a
byte copy of the root one (setuptools picks it up into `*.dist-info/licenses/`). Copy it again after any change
to the root file; `tests/test_licensing_docs_sync.py` fails on drift.

| project | location | ships |
|---|---|---|
| `assay` | `assay/` | wheel |
| `ciu` | `ciu/` | wheel |
| `cmru` | `cmru/` | wheel |
| `cli-extended` | `libraries/cli-extended/` | wheel |
| `iobench` | `libraries/iobench/` | wheel |
| `patchseries` | `libraries/patchseries/` | in-repo library |
| `vmscreen` | `libraries/vmscreen/` | in-repo library |
| `nyxloom` | `nyxloom/` | wheel |
| `pwmcp-client` | `pwmcp/client/` | wheel (also inside the pwmcp bundle) |
| `run-gate` | `run-gate-project/` | wheel |
| `cgprofile` | `scripts/cgroup-profiler/` | wheel |
| `topos` | `topos/` | wheel |
| `tnt` | `truenas/tnt/` | wheel |

Archived experiments under `legacy-experiments/` are not shipped and are exempt.

## Images and bundles

Images (`tester-base`, `tester-unified`, `mdt-cockpit`, `pwmcp`, `pwmcp-ocr`, `nyxloomd`, `cgprofile`) and the
pwmcp / tls-edge / empyrion bundles contain third-party software under many licences (Debian packages, npm
packages, Playwright and Chromium, Tesseract, tools downloaded by version). The `org.opencontainers.image.licenses`
label (`OCI_LICENSES`) describes the first-party layer only. Each image build stage that installs third-party
software also writes the real closure:

```
python3 scripts/licensing/third_party_notices.py python > /usr/share/doc/THIRD-PARTY-NOTICES.md
python3 scripts/licensing/third_party_notices.py dpkg  >> /usr/share/doc/THIRD-PARTY-NOTICES.md
```

npm and downloaded binaries are listed by hand in the audit report until a generator covers them.

## Vendored and unusual items

* `scripts/debian-install-v2/debian_install_v2/vendor/iocost_coef_gen.py` and its patched generated copy
  `scripts/debian-install-v2/tools/iocost_coef_gen.py`: Linux kernel tool, GPL-2.0, Copyright Tejun Heo, Andy Newell,
  Facebook. Stays GPL-2.0 (patches are derivative). It is not in any wheel; the repository source carries it
  with its header. Do not copy it into an MIT wheel.
* `scripts/cgroup-profiler/src/cgprofile/_damon/damon_analysis.py` and `scripts/damon-analysis/**`: first-party,
  MIT (changed from `GPL-2.0` on 2026-11-05 after the owner confirmed authorship; the `damo` tool is only run as a
  subprocess).
* `pwmcp/tools/glyphgrid/NOTICE.md`: font-derived bitmap atlases (GPL-2.0 kernel font, Unifont, OFL-1.1 Terminus,
  public domain fixed, MIT/X11 VGA, BSD-2-Clause-Patent EDK2). Not legally reviewed; the atlases are not in any
  published image today. Do not add them to an image before that review.
* `ciu[ssh]` pulls `paramiko` (LGPL-2.1) and `tnt` installs `truenas_api_client` (LGPL-3.0) as separately
  installed, unmodified distributions.

## Adding a project

Add `license = "MIT"` to its `pyproject.toml`, copy the root `LICENSE` next to it, and list it above. A new
SPDX header must be `MIT` unless the file is listed in `THIRD_PARTY_SPDX` in the sync test.
