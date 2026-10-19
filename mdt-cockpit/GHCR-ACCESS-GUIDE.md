# GitHub Container Registry access

## Package names and tags

MDT Cockpit publishes two GHCR package families:

- Native-host image: `ghcr.io/volkb79-2/mdt-cockpit`
- VS Code Dev Containers image: `ghcr.io/volkb79-2/mdt-cockpit-devcontainer`

Tags follow `<debian>-py<python>[-<flavor>]-<date>[-<counter>]`, with matching
`latest` aliases. PHP 8.5 remains a tag flavor on those same two packages.
The Bake target and flavor names are listed in the project
[README](README.md#tags-and-variants).

The new `mdt-cockpit` package families have not been published yet. The dated
examples and package pages from the original MDT project are not pullable under
these new package names. CMRU generates this fork's package manifests from its
first successful image build.

## Pull access

Public packages can be pulled without credentials. For a private package, log
in on the machine that runs Docker with a token that has `read:packages`:

```bash
echo "$GHCR_READ_TOKEN" | docker login ghcr.io \
  --username "$GITHUB_USERNAME" --password-stdin
docker pull ghcr.io/volkb79-2/mdt-cockpit-devcontainer:<tag>
```

VS Code Dev Containers asks the host Docker daemon to pull the image. Host GHCR
credentials therefore need to be available to the host Docker client; they do
not need to be copied into the container.

## Release access

CMRU obtains release credentials from the repository's configured secret source.
The release identity needs `write:packages`; after publishing, CMRU mirrors the
GHCR package visibility to the source repository. Do not put a token in this
document, a devcontainer image, or a committed `.env` file.

## Troubleshooting

- `denied: permission_denied`: authenticate the host Docker client and check
  that the token has `read:packages`.
- `manifest unknown`: verify the package family and tag against the published
  package page. A tag from the original `modern-debian-tools-python-debug`
  family is not automatically available under the fork's package names.

Package pages:

- <https://github.com/volkb79-2/vbpub/pkgs/container/mdt-cockpit>
- <https://github.com/volkb79-2/vbpub/pkgs/container/mdt-cockpit-devcontainer>

See [GitHub's GHCR documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
for token scopes and registry login details.
