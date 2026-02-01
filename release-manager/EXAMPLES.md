# Release Manager Examples

## 1) Minimal release.toml (repo root)

```toml
repo_root = "."

[github]
username = "your-org-or-user"
repo = "vbpub"
token = "ghp_..."

[registry]
url = "ghcr.io"

[env]
CIU_LATEST_TAG = "ciu-wheel-latest"
CIU_LATEST_ASSET_NAME = "ciu-wheel-latest-py3-none-any.whl"

[orchestration]
project_order = ["ciu", "vsc-devcontainer", "playwright-mcp"]
default_projects = ["ciu", "vsc-devcontainer", "playwright-mcp"]
default_steps = ["run-tests", "build", "push", "validate"]

[cleanup]
release_tag_prefixes = [
  "ciu-wheel-",
  "playwright-mcp-client-wheel-",
  "playwright-mcp-stack-bundle-",
]
keep_release_tags = [
  "ciu-wheel-latest",
  "playwright-mcp-client-wheel-latest",
  "playwright-mcp-stack-bundle-latest",
]
ghcr_packages = ["vsc-devcontainer", "playwright-mcp"]
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
  --project vsc-devcontainer --build
```

## 5) Use build-push.toml directly

```bash
python3 /workspaces/vbpub/release-manager/src/release_manager/step_runner.py \
  --config /workspaces/vbpub/vsc-devcontainer/build-push.toml \
  --step build-images
```

## 6) Build a stack bundle (playwright-mcp)

```bash
python3 /workspaces/vbpub/playwright-mcp/release-stack-bundle.py
```
