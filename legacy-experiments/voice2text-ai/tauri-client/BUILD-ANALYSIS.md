# Build Analysis and Solution

## Why Two Dockerfiles?

### 1. **Dockerfile.windows** (Original - November 12)
- **Purpose**: Build environment with volume mount strategy
- **Pattern**: Mount host directory into container
- **Usage**: `docker run -v $(pwd):/app tauri-windows-builder`
- **Problem**: Docker-in-Docker volume mount issue (empty /app in container)

### 2. **Dockerfile.windows-build** (Created December 11)
- **Purpose**: Self-contained build with files copied into image
- **Pattern**: COPY files during image build
- **Usage**: `docker run tauri-win-build`
- **Problem**: Output stays inside image, lost when container exits

## The Core Problem

Both Dockerfiles build to **internal container paths**:
```
/app/src-tauri/target/x86_64-pc-windows-gnu/release/*.exe
```

**Without an output volume mount**, these files are lost when the container exits!

## Why Build Failed/Hung

The build hung at "Installing Node.js dependencies..." because:
1. npm tried to download 100+ transitive dependencies
2. Running inside Docker without proper registry/cache
3. No visible progress output (buffered)
4. Eventually timed out or was killed

## Where Are the Logs?

Logs only exist during container lifetime:
- `docker logs <container-id>` - only while container running
- Once container exits (especially with `--rm`), logs are GONE
- **Solution**: Use `tee` to save logs to host file

## Current Status: No Build Output Exists

```bash
$ docker run --rm tauri-win-build:latest -c "ls /app/target/..."
No .exe files in image
```

**Why**: The Dockerfile builds the image, but doesn't run the build during image creation.
The ENTRYPOINT runs the build when the container starts, then output is lost.

## The Fix: Proper Output Volume Strategy

Since you're in the `docker` group, you CAN run builds properly!

### Option A: Use Original Dockerfile with Fixed Volume Mount

```bash
# Build with proper volume mounts for output
docker run --rm \
  -v $(pwd):/app \
  -v /app/node_modules \
  -v /app/src-tauri/target \
  --workdir /app \
  tauri-windows-builder:latest
```

**Problem**: Still has the Docker-in-Docker volume mount issue we saw earlier.

### Option B: Run Build, Then Copy Artifacts Out

```bash
# Run build without --rm (keep container)
docker run --name tauri-build-$(date +%s) tauri-win-build:latest

# Check if build succeeded
docker ps -a --filter "name=tauri-build" --format "{{.Names}} {{.Status}}"

# Copy artifacts out to host
docker cp tauri-build-123456:/app/src-tauri/target/x86_64-pc-windows-gnu/release/ ./dist/

# Cleanup
docker rm tauri-build-123456
```

### Option C: Mount Output Directory (Recommended)

Create a hybrid approach:

```bash
# Create output directory on host
mkdir -p ./dist

# Run with output volume mounted
docker run --rm \
  -v $(pwd)/dist:/output \
  tauri-win-build:latest \
  /bin/bash -c "
    cd /app && \
    npm install && \
    npm run build && \
    cd src-tauri && \
    cargo tauri build --target x86_64-pc-windows-gnu && \
    cp target/x86_64-pc-windows-gnu/release/*.exe /output/ 2>/dev/null && \
    echo 'Build complete! Output in ./dist/'
  "
```

## Recommended Solution: New Approach

Since Docker-in-Docker has issues, let's use the host system directly:

### You Have:
- ✅ Docker group membership (can run Docker)
- ✅ Rust likely installed (check: `rustc --version`)
- ✅ Node.js likely installed (check: `node --version`)

### Build Natively on Host (Fastest):

```bash
# Install Windows target
rustup target add x86_64-pc-windows-gnu

# Install MinGW for cross-compilation
sudo apt-get install mingw-w64

# Configure Cargo for MinGW
mkdir -p ~/.cargo
cat >> ~/.cargo/config.toml << 'EOF'
[target.x86_64-pc-windows-gnu]
linker = "x86_64-w64-mingw32-gcc"
ar = "x86_64-w64-mingw32-ar"
EOF

# Build!
cd /workspaces/dstdns/legacy-experiments/voice2text-ai/tauri-client
npm install
npm run build
cd src-tauri
cargo tauri build --target x86_64-pc-windows-gnu

# Output will be at:
# src-tauri/target/x86_64-pc-windows-gnu/release/*.exe
```

## Answering Your Questions

### Q: Why two Dockerfile files?
**A**: One was original (volume mount strategy), second was created when volume mounts failed (copy strategy). Both have issues with output persistence.

### Q: How did the build go?
**A**: It hung at npm install and never completed. No .exe was produced.

### Q: Where is build.sh output persisted?
**A**: **Nowhere** - output is inside container which was destroyed (--rm flag).

### Q: Can we see this in docker logs?
**A**: **No** - containers were run with --rm, so logs were deleted when container exited.

### Q: I have to run on host?
**A**: **Not required**, but it's the EASIEST solution since you're in docker group and can install the tools directly.

## Next Steps

1. **Quick Test**: Check if tools are already installed on host
   ```bash
   rustc --version && cargo --version && node --version
   ```

2. **If Yes**: Build natively (fastest, no Docker issues)

3. **If No**: Use Option B (build in container, copy artifacts out)

4. **Security**: After building, run npm audit on the output directory

