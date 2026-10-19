# Preventing another misplaced BuildKit

For your stated priority and use case “background batch processing that cannot hurt interactive/prod latency”, this combination is interesting:

BuildKit huge cold working set
        |
        +--> physical RAM <= 1.5G, steer with cgroup MemoryHigh=1G, MemoryMax=1.5G
        |
        +--> zswap <= 128M-1024M
        |
        +--> backing swap can be huge tens of GB
                  |
                  +--> swap IOPS strongly capped
        |
        +--> CPUQuota=500% but CPUWeight=10, “yield CPU aggressively when sibling cgroups compete”
        |
        +--> max-parallelism = 1, BuildKit jobs strongly serialized


I would **not** make `docker build` a no-op. You already built a better solution: the persistent `mdt-buildkitd` container is correctly placed under:

```text
/dev.slice/dev-buildkitd.slice/
```

and the slice has the intended `MemoryHigh=1G`, `MemoryMax=1.5G`, huge swap allowance, and its own resource controls. 

Make that daemon the **only normal BuildKit backend**.

The clean way is a Buildx **remote driver** pointed at your existing managed `mdt-buildkitd` container. Docker supports exactly this, including a `docker-container://container-name` endpoint; importantly, the remote driver does **not create another BuildKit container**. ([Docker Documentation][4])

Create it once:

```bash
docker buildx create \
    --name mdt-managed \
    --driver remote \
    --driver-opt default-load=true \
    docker-container://mdt-buildkitd
```

Then verify:

```bash
docker buildx inspect mdt-managed
```

and select it:

```bash
docker buildx use mdt-managed
```

`default-load=true` is useful here so ordinary local-image builds behave more like normal `docker build`.

Then the crucial setting:

```bash
export BUILDX_BUILDER=mdt-managed
```

Unlike merely running `docker buildx use`, this also matters for ordinary:

```bash
docker build ...
```

Docker documents an important subtlety: `docker build` normally insists on the Engine's default builder for backwards compatibility, but `BUILDX_BUILDER` or explicit `--builder` overrides that. ([Docker Documentation][5])

So in your host setup I'd install something like:

```bash
# /etc/profile.d/mdt-buildkit.sh
export BUILDX_BUILDER=mdt-managed
```

and inject the same variable into your devcontainers/CI/build environments.

That turns:

```bash
docker build .
```

into a build targeting the managed builder, without people having to remember anything.

### Add a convenience command too

I would install `/usr/local/bin/mdt-build`:

```bash
#!/usr/bin/env bash
set -euo pipefail

BUILDER=${MDT_BUILDER:-mdt-managed}

if ! /usr/bin/docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
    echo "ERROR: managed BuildKit builder '$BUILDER' is unavailable" >&2
    exit 70
fi

exec /usr/bin/docker build \
    --builder "$BUILDER" \
    "$@"
```

Then project build scripts can use:

```bash
mdt-build -t foo:latest .
```

instead of caring how BuildKit is provisioned.

### Why this is better than creating another `docker-container` builder

This is particularly important on your host.

Docker documents the `docker-container` driver's `cgroup-parent` option as applying when Docker uses the **cgroupfs** driver. Your Docker is using systemd cgroups. ([Docker Documentation][3])

So trying to solve this by saying:

```bash
docker buildx create \
  --driver docker-container \
  --driver-opt cgroup-parent=dev-buildkitd.slice
```

is not the architecture I'd rely on here.

You already have the accurately placed container. Use the **remote driver to connect to it** instead of asking Buildx to manufacture another container.

## Add a guard against accidental Buildx-managed builders

The next layer I'd put into `host-setup` is a checker:

```bash
#!/usr/bin/env bash
set -euo pipefail

EXPECTED_PARENT=dev-buildkitd.slice

for cfg in /var/lib/docker/containers/*/config.v2.json; do
    [[ -r "$cfg" ]] || continue

    dir=${cfg%/*}
    id=${dir##*/}

    image=$(jq -r '.Config.Image // .Image // ""' "$cfg" 2>/dev/null || :)
    name=$(jq -r '.Name // ""' "$cfg" 2>/dev/null || :)
    name=${name#/}

    case "$name:$image" in
        buildx_buildkit_*:*|*:moby/buildkit*)
            parent=$(jq -r \
                '.HostConfig.CgroupParent // ""' \
                "$cfg" 2>/dev/null || :)

            if [[ "$parent" != "$EXPECTED_PARENT" ]]; then
                printf >&2 \
                    'MISPLACED BUILDKIT: %-40s id=%s parent=%q\n' \
                    "$name" "${id:0:12}" "$parent"
            fi
            ;;
    esac
done
```

But I would make it inspect through Docker when Docker is healthy rather than depend permanently on Docker's internal JSON layout:

```bash
docker ps -aq |
while read -r id; do
    read -r name image parent < <(
        docker inspect \
            --format '{{.Name}} {{.Config.Image}} {{.HostConfig.CgroupParent}}' \
            "$id"
    )

    name=${name#/}

    case "$name:$image" in
        buildx_buildkit_*:*|*:moby/buildkit*)
            if [[ "$parent" != dev-buildkitd.slice ]]; then
                echo "MISPLACED: $name $id parent=$parent" >&2
            fi
            ;;
    esac
done
```

You could run this from your host-setup health check and fail validation if any such container exists.

For stronger active enforcement, a tiny systemd service can listen to `docker events` for container creation/start and immediately reject/kill a `buildx_buildkit_*` container that isn't the explicitly managed `mdt-buildkitd`. That's more effective than an alias because aliases/functions can be bypassed by scripts or `/usr/bin/docker`.

### MemoryZSwapMax

Another angle, When the limit is reached, that cgroup's new swap pages stop being admitted into the compressed in-RAM zswap pool until space becomes available/writeback happens.

For example, you could test:

```bash
MemoryZSwapMax=128M
MemoryZSwapWriteback=yes
```

or, aggressively:

``` bash
MemoryZSwapMax=0
MemoryZSwapWriteback=yes
```

The latter says, effectively: don't burn significant RAM/CPU compressing this background build's swap; send it toward the real swap backing device instead.

### BuildKit's own concurrency control

For the Buildx docker-container driver, use a BuildKit config:

# /etc/buildkit/buildkitd.toml

```toml
[worker.oci]
max-parallelism = 1
```

Then create/configure the builder with that config. Docker documents `--buildkitd-config` for this.


### I would use these layers

1. **`mdt-buildkitd` is the only managed BuildKit daemon**, in `dev-buildkitd.slice`.
2. Create `mdt-managed` as a Buildx `remote` builder pointing to `docker-container://mdt-buildkitd`.
3. Export `BUILDX_BUILDER=mdt-managed` in host/devcontainer/CI environments.
4. Provide `mdt-build` as the boring, explicit convenience command.
5. Have host-setup detect `buildx_buildkit_*` containers and flag them as configuration errors.
6. Optionally add a Docker-event guard that removes accidental Buildx-managed builder containers.

I would **not alias `docker build` to no-op**. Transparently routing it to `mdt-managed` via `BUILDX_BUILDER` gives you the safety property without breaking ordinary tools. Docker explicitly supports that override even for `docker build`. ([Docker Documentation][5])

One additional thing I'd fix in your host setup: the correctly placed `mdt-buildkitd` currently reports its child container with `memory.swap.max≈16G`, while its parent `dev-buildkitd.slice` allows ~45G.  Since your intended design is explicitly “very small RAM, potentially huge disk swap”, that **16 GiB leaf limit becomes the effective swap ceiling before the slice's ~45 GiB ceiling does**. If you really want the full cascade allowance, I'd trace where that per-container 16G limit is being injected and remove/raise it.

[1]: https://man7.org/linux/man-pages/man5/systemd.resource-control.5.html?utm_source=chatgpt.com "systemd.resource-control(5) - Linux manual page"
[2]: https://cdn.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html?utm_source=chatgpt.com "Control Group v2 — The Linux Kernel documentation"
[3]: https://docs.docker.com/build/builders/drivers/docker-container/?utm_source=chatgpt.com "Docker container driver | Docker Docs"
[4]: https://docs.docker.com/build/builders/drivers/remote/?utm_source=chatgpt.com "Remote driver | Docker Docs"
[5]: https://docs.docker.com/build/builders/?utm_source=chatgpt.com "Builders | Docker Docs"

