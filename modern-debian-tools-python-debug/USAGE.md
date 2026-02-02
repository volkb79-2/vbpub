# Devcontainer Image Usage

## Build (local)

Build all four variants:

- `./build-images.py`

Environment configuration:
- Copy `.env.sample` to `.env` and adjust values as needed.

Optional overrides (environment variables):
- `REGISTRY`, `GITHUB_USERNAME`, `BUILD_DATE`, `BACKPORTS_URI`, `CIU_LATEST_TAG`, `CIU_LATEST_ASSET_NAME`

Latest CIU wheel asset scheme:
- https://github.com/volkb79-2/vbpub/releases/download/ciu-wheel-latest/ciu-<version>-py3-none-any.whl

Any variable in docker-bake.hcl can be overridden for a build.

Example:

```
B2_VERSION=4.5.0 ./build-images.py
```

## Push (registry)

After validation, push all variants:

- `./push-images.py`

Ensure you are logged in to the registry (e.g., `docker login ghcr.io`) and that `GITHUB_USERNAME` matches your org/user.
If `GITHUB_PUSH_PAT` and `GITHUB_USERNAME` are set in `.env`, the push script will log in automatically.

## Use in devcontainer.json

Reference the desired tag under `image` (no `build` section):

```
{
  "image": "ghcr.io/acme/modern-debian-tools-python-debug-vsc-devcontainer:bookworm-py3.13-20260117",
  "remoteUser": "vscode"
}
```

Counterexample (do NOT do this):

```
{
  "build": {
    "dockerfile": "Dockerfile"
  }
}
```

## Manifest

Each image writes a manifest file at:

```
/home/vscode/devcontainer-manifest.txt
```

This includes tool versions, pip package list, and selected Debian package versions.
