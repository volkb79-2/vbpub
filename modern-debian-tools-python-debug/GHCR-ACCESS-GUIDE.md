# GitHub Container Registry (GHCR) Access Guide

## Image Privacy Status

### When is public/private decided?
- **Images are PRIVATE by default** when pushed to GHCR
- Privacy is controlled in GitHub's package settings (web UI), **not during push**
- The `docker push` command does not set visibility

### How to make images public:

1. **Navigate to package settings:**
  - URL pattern: `https://github.com/users/volkb79-2/packages/container/modern-debian-tools-python-debug/settings`
   - Or: GitHub Profile → Packages → Select package → Settings

2. **Change visibility:**
   - Scroll to "Danger Zone"
   - Click "Change visibility"
   - Select "Public"
   - Confirm the action

### Published Images:
```bash
# Available images (currently private):
ghcr.io/volkb79-2/modern-debian-tools-python-debug:bookworm-py3.11-20260118
ghcr.io/volkb79-2/modern-debian-tools-python-debug:bookworm-py3.13-20260118
ghcr.io/volkb79-2/modern-debian-tools-python-debug:trixie-py3.11-20260118
ghcr.io/volkb79-2/modern-debian-tools-python-debug:trixie-py3.13-20260118

# VS Code devcontainer variants:
ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:bookworm-py3.11-20260118
ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:bookworm-py3.13-20260118
ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.11-20260118
ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.13-20260118
```

---

## Authentication for Private Images

### Where credentials are used:
1. **Host machine** - When you run `docker login ghcr.io`
2. **VSCode Devcontainer** - Automatically inherits host's Docker credentials
3. **CI/CD pipelines** - Need explicit authentication in workflow

### Credential storage:
```bash
# Host credentials stored in:
~/.docker/config.json

# Example content:
{
  "auths": {
    "ghcr.io": {
      "auth": "base64_encoded_username:token"
    }
  }
}
```

### How to authenticate:

**Option 1: Using Personal Access Token (PAT) - Recommended**
```bash
# Create PAT at: https://github.com/settings/tokens
# Required scope: write:packages, read:packages

echo $GITHUB_PUSH_PAT | docker login ghcr.io -u $GITHUB_USERNAME --password-stdin
```

**Option 2: Using password from .env**
```bash
# From modern-debian-tools-python-debug directory:
source .env
echo $GITHUB_PUSH_PAT | docker login ghcr.io -u $GITHUB_USERNAME --password-stdin
```

**Verify authentication:**
```bash
docker pull ghcr.io/volkb79-2/modern-debian-tools-python-debug:bookworm-py3.13-20260118
```

---

## VSCode Devcontainer Access

### How VSCode uses credentials:

1. **Automatic inheritance:**
   - VSCode runs `docker` commands on your **host machine**
   - Uses host's Docker daemon (`/var/run/docker.sock`)
   - Automatically uses credentials from host's `~/.docker/config.json`

2. **No additional configuration needed:**
   - If you've logged in on the host, devcontainer has access
   - No need to pass credentials into the container
   - Works for both building and pulling images

### Using pre-built images in devcontainer.json:

**Before (local build):**
```jsonc
{
  "name": "my-devcontainer",
  "build": {
    "dockerfile": "Dockerfile",
    "args": {
      "PYTHON_VERSION": "3.13"
    }
  }
}
```

**After (pre-built image):**
```jsonc
{
  "name": "my-devcontainer",
  "image": "ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:bookworm-py3.13-20260118",
  // Optional: Keep build config commented for fallback
  // "build": {
  //   "dockerfile": "Dockerfile"
  // }
}
```

### Benefits of pre-built images:

✅ **Faster startup** - No build time, just pull
✅ **Consistent** - Same image across team/machines
✅ **Versioned** - Tagged with date for reproducibility
✅ **Bandwidth efficient** - Pull once, reuse many times

---

## Troubleshooting

### Error: "denied: permission_denied"
**Cause:** Not authenticated or wrong credentials

**Solution:**
```bash
# Re-authenticate with correct credentials
docker logout ghcr.io
echo $GITHUB_TOKEN | docker login ghcr.io -u volkb79-2 --password-stdin
```

### Error: "manifest unknown"
**Cause:** Image name or tag doesn't exist

**Solution:**
```bash
# List available tags:
# Visit: https://github.com/volkb79-2/vbpub/pkgs/container/modern-debian-tools-python-debug
# Also: https://github.com/volkb79-2/vbpub/pkgs/container/modern-debian-tools-python-debug-vsc-devcontainer

# Or use GitHub API:
curl -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/users/volkb79-2/packages/container/modern-debian-tools-python-debug/versions
```

### Devcontainer can't pull private image
**Cause:** Host machine not authenticated

**Solution:**
```bash
# Exit devcontainer (if inside)
# On HOST machine:
docker login ghcr.io -u volkb79-2

# Then rebuild devcontainer in VSCode:
# Cmd/Ctrl + Shift + P → "Rebuild Container"
```

---

## Best Practices

### For development:
1. **Keep images private** during development
2. **Use date-based tags** for versioning (e.g., `20260118`)
3. **Document required authentication** in README
4. **Use .env for credentials** (never commit tokens!)

### For production/public:
1. **Make image public** if sharing with community
2. **Add README to package** explaining usage
3. **Use semantic versioning** tags (e.g., `v1.2.3`)
4. **Test with fresh auth** to verify access

### For CI/CD:
```yaml
# GitHub Actions example:
- name: Login to GHCR
  uses: docker/login-action@v2
  with:
    registry: ghcr.io
    username: ${{ github.actor }}
    password: ${{ secrets.GITHUB_TOKEN }}

- name: Pull devcontainer image
  run: docker pull ghcr.io/volkb79-2/modern-debian-tools-python-debug:bookworm-py3.13-20260118
```

---

## Reference Links

- **Package Settings:**
  - https://github.com/volkb79-2/vbpub/pkgs/container/modern-debian-tools-python-debug/settings
  - https://github.com/volkb79-2/vbpub/pkgs/container/modern-debian-tools-python-debug-vsc-devcontainer/settings
- **PAT Creation:** https://github.com/settings/tokens
- **GHCR Documentation:** https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
- **VSCode Devcontainers:** https://code.visualstudio.com/docs/devcontainers/containers
