# vbpub release manager

Unified release orchestration for vbpub projects.

## Config-driven usage

The release manager is config-driven. Provide a TOML config file that defines:

- `repo_root`
- project order and default steps under `[orchestration]`
- project commands under `[projects.<name>.steps.<step>.commands]`
- cleanup rules under `[cleanup]`

The tool is intentionally agnostic to repo-specific scripts and only executes
the commands defined in the config.

### Required configuration

Use the provided samples as a starting point and store the real config at repo root:

- [vbpub/release.sample.toml](../release.sample.toml) (repo defaults, no secrets)
- [release-manager/release.sample.toml](release.sample.toml) (syntax/spec only)
- vbpub/release.toml (local config with secrets; gitignored)

### Running

Set the config path explicitly (fail-fast if missing). Secrets now live in vbpub/release.toml (not .env). All release/build entrypoints are Python:

```bash
RELEASE_MANAGER_CONFIG=/workspaces/vbpub/release.toml \
python3 /workspaces/vbpub/release-all.py --run-tests --build --push --validate
```

Or pass it directly:

```bash
python3 /workspaces/vbpub/release-all.py \
	--config /workspaces/vbpub/release.toml \
	--run-tests --build --push --validate
```

### Release version auto-increment

The release manager now auto-increments `BUILD_DATE` per day to avoid
overwriting versioned tags when multiple releases run on the same day.

- First release of the day: `YYYYMMDD`
- Second release: `YYYYMMDD.1`
- Third release: `YYYYMMDD.2`

Override behavior with environment variables:

- `RELEASE_AUTO_INCREMENT=0` to disable incrementing
- `RELEASE_DATE_ENV` (default: `BUILD_DATE`)
- `RELEASE_DATE_FORMAT` (default: `%Y%m%d`)
- `RELEASE_COUNTER_DIR` (default: `logs/`)
- `RELEASE_COUNTER_TEMPLATE` (default: `release-counter-{date}.txt`)

### Cleanup by age

Cleanup rules are read from the config, and the age cutoff is supplied at runtime:

```bash
python3 /workspaces/vbpub/release-all.py \
	--config /workspaces/vbpub/release.toml \
	--remove-assets 7d
```

### Orchestration execution modes

The release manager can run steps in two ways:

- `step-first` (default): for each step, run all selected projects in order.
- `project-first`: for each project, run all selected steps in order.

Use `orchestration.execution_mode` to control this.

### Per-step project ordering

When execution mode is `step-first`, you can override project order per step with:

```toml
[orchestration.step_project_order]
run-tests = ["ciu", "modern-debian-tools-python-debug", "pwmcp"]
build = ["ciu", "modern-debian-tools-python-debug", "pwmcp"]
push = ["ciu", "modern-debian-tools-python-debug", "pwmcp"]
validate = ["ciu", "modern-debian-tools-python-debug", "pwmcp"]
```

This is useful when a downstream project needs artifacts produced by an upstream project in the same step.

### OCI image description

OCI_DESCRIPTION values are Markdown. Include a short tool manifest summary and
reference the in-image manifest at:

- /usr/local/share/modern-debian-tools-python-debug/manifest.md

### Per-project build/push config

Each project has its own build-push.toml that defines steps and commands:

- [ciu/build-push.toml](../ciu/build-push.toml)
- [modern-debian-tools-python-debug/build-push.toml](../modern-debian-tools-python-debug/build-push.toml)
- [pwmcp/build-push.toml](../pwmcp/build-push.toml)

### Examples

See [release-manager/EXAMPLES.md](EXAMPLES.md) for practical examples.


# Release on github with multipackage repo

Current Release Flow:
- **CIU wheel publish**: creates a versioned release tag `ciu-wheel-<version>` and **recreates** `ciu-wheel-latest` so it becomes the newest release. See vbpub/ciu/tools/publish-wheel-release.py.
- **PWMCP client wheel**: creates `pwmcp-client-wheel-<version>` and **recreates** `pwmcp-client-wheel-latest`. See vbpub/pwmcp/publish-client-wheel.py.
- **PWMCP server wheel**: creates `pwmcp-server-wheel-<version>` and **recreates** `pwmcp-server-wheel-latest`. See vbpub/pwmcp/publish-server-wheel.py.

How vsc‑devcontainer and dstdns consume latest:
- **modern-debian-tools-python-debug**: uses `CIU_LATEST_TAG`/`CIU_LATEST_ASSET_NAME` during build to fetch the wheel from the **tagged latest** release (`/releases/tags/<tag>`). Recreating `ciu-wheel-latest` ensures it always resolves to the newest build.
- **dstdns env‑setup**: it **only** downloads from `CIU_PKG_URL` (no discovery). See dstdns/.github/actions/env-setup.sh. You must set `CIU_PKG_URL` to the package‑specific latest asset URL (e.g. the `CIU_WHEEL_LATEST_URL` output from the publish step), not the repo‑wide `/releases/latest`.

Best‑practice guidance (multi‑package repos):
- Use **versioned tags per package** (you already do).
- Maintain **package‑specific latest tags** (you now recreate them).
- Avoid `/releases/latest` for anything package‑specific; it’s repo‑wide.
- For consumption, prefer **`/releases/tags/<package>-latest`** or a **fixed asset URL** emitted by your release tooling (like `CIU_WHEEL_LATEST_URL`).
- Optionally publish wheels to a package registry (PyPI or GH Packages) to separate packages cleanly; keep GitHub Releases for bundles and human‑friendly artifacts.

