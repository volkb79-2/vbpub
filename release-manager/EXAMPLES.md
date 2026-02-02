# Release Manager Examples

## 1) Minimal release.toml (repo root)

Use vbpub/release.sample.toml as the base template (no secrets), then copy it
into vbpub/release.toml and fill in secrets.

```toml
repo_root = "."

[github]
username = "your-org-or-user"
repo = "vbpub"
token = "<GITHUB_TOKEN>"

[registry]
url = "ghcr.io"

[env]
OCI_VENDOR = "Volker Badziong"
OCI_DESCRIPTION = """# Modern Debian Tools + Python Debug

Prebuilt devcontainer image focused on CIU workflows and modern CLI tooling.

## Tool manifest
The full tool manifest is embedded in the image at:
`/usr/local/share/modern-debian-tools-python-debug/manifest.md`
"""
CIU_LATEST_TAG = "ciu-wheel-latest"
CIU_LATEST_ASSET_NAME = "ciu-wheel-latest-py3-none-any.whl"

[orchestration]
project_order = ["ciu", "modern-debian-tools-python-debug", "pwmcp"]
default_projects = ["ciu", "modern-debian-tools-python-debug", "pwmcp"]
default_steps = ["run-tests", "build", "push", "validate"]
execution_mode = "project-first"

[orchestration.step_project_order]
run-tests = ["ciu", "modern-debian-tools-python-debug", "pwmcp"]
build = ["ciu", "modern-debian-tools-python-debug", "pwmcp"]
push = ["ciu", "modern-debian-tools-python-debug", "pwmcp"]
validate = ["ciu", "modern-debian-tools-python-debug", "pwmcp"]

[cleanup]
release_tag_prefixes = [
  "ciu-wheel-",
  "pwmcp-client-wheel-",
  "pwmcp-server-wheel-",
]
keep_release_tags = [
  "ciu-wheel-latest",
  "pwmcp-client-wheel-latest",
  "pwmcp-server-wheel-latest",
]
ghcr_packages = ["modern-debian-tools-python-debug", "modern-debian-tools-python-debug-vsc-devcontainer", "pwmcp-server", "pwmcp-client"]
```

## 2) Running the full pipeline

```bash
python3 /workspaces/vbpub/release-all.py \
  --config /workspaces/vbpub/release.toml \
  --run-tests --build --push --validate
```

## 2b) Run via release-runner (logs + cleanup)

```bash
python3 /workspaces/vbpub/release-runner.py
```

## 3) Cleanup by age (releases + GHCR)

```bash
python3 /workspaces/vbpub/release-all.py \
  --config /workspaces/vbpub/release.toml \
  --remove-assets 7d
```

## 4) Build a single project only

```bash
python3 /workspaces/vbpub/release-all.py \
  --config /workspaces/vbpub/release.toml \
  --project modern-debian-tools-python-debug --build
```

## 5) Use build-push.toml directly

```bash
python3 /workspaces/vbpub/release-manager/src/release_manager/step_runner.py \
  --config /workspaces/vbpub/modern-debian-tools-python-debug/build-push.toml \
  --step build-images
```
