# mdt - known issues, TODO, and backlog

## MDT-001 - requirements for `aider-chat`

starting `MDT_IMAGE_VERSION="20261002"`, `IMAGE_MANIFEST="/usr/local/share/modern-debian-tools-python-debug/manifest.md"` gives this startup info

```
[INFO] Using requirements file: /workspaces/dstdns/requirements.txt
ERROR: pip's dependency resolver does not currently take into account all the packages that are installed. This behaviour is the source of the following dependency conflicts.
aider-chat 0.86.3.dev53+g5dc9490bb requires aiohttp==3.13.3, but you have aiohttp 3.14.3 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires charset-normalizer==3.4.6, but you have charset-normalizer 3.5.2 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires click==8.3.1, but you have click 8.5.0 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires fastapi==0.135.1, but you have fastapi 0.142.2 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires packaging==26.0, but you have packaging 26.3 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires pydantic==2.12.5, but you have pydantic 2.13.5 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires pydantic-core==2.41.5, but you have pydantic-core 2.46.5 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires pygments==2.19.2, but you have pygments 2.21.0 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires requests==2.32.5, but you have requests 2.34.2 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires rich==14.3.3, but you have rich 15.0.0 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires urllib3==2.6.3, but you have urllib3 2.8.0 which is incompatible.
[SUCCESS] Python packages installed from /workspaces/dstdns/requirements.txt
```

## MDT-002  - docker daemon default-address-pools

host-setup should configure 
```json
{
  "default-address-pools": [
    { "base": "10.240.0.0/16", "size": 24 }
  ]
}
```

## MDT-003 - integrate `fs.inotify.max_user_watches`

see `modern-debian-tools-python-debug/host-setup/etc/sysctl.d/99-inotify.conf`

## MDT-004 - move to a persisted `/home/vscode` in mdt-cockpit

After the current MDT host and environment work is complete, fork this project
to `vbpub/mdt-cockpit` and make all of `/home/vscode` a host-persisted mount.
First inventory every shipped image path and every persistent home path, then
decide which content belongs in the image, in user configuration, or in
host-persisted runtime state. In particular, audit tools and runtimes installed
under `~`, agent settings under `~/.config` and `~/.local`, and files that a
home mount would hide from the image. The migration must preserve executable
discovery, seed only intentional defaults, and explain how users move existing
state without silently replacing it.

Deliver a consumer guide with reusable rules for choosing image paths,
configuration paths, caches, secrets, and persisted state. Its acceptance
inventory must cover every current `/home/vscode` file and directory and name
the owner and persistence policy for each one before the mount changes.

## MDT-005 - adopt `cli-extended` for grammer, wizard, sematic check, ... 

vbpub has a library to prohibit handrolling solutions every CLI needs. adopt it.
