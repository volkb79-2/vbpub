# Linux Desktop Architecture & Scaling Diagnostics

This document describes common Linux desktop architectures, how display
scaling works in each, and explains what `scripts/linux-desktop-analysis.py`
does and why.

---

## 1. Linux Desktop Architectures

### 1.1 X11 (X.Org / X Window System)

X11 is the traditional Linux display server protocol, in use since the
1980s.  Applications talk to the X server, which composites all windows
and drives the display.

**Scaling on X11**

| Method | Tool / API | Integer only? |
|---|---|---|
| Global scale via DPI | `xrandr --dpi` / Xft.dpi | No — but fractional support is poor |
| Per-output transform | `xrandr --output … --scale` | No |
| Toolkit-level (GTK) | `GDK_SCALE`, `GDK_DPI_SCALE` | GTK3+ supports fractional |
| Toolkit-level (Qt) | `QT_SCALE_FACTOR` | No |

**Limitations** — X11 scaling is uneven across toolkits.  Blurry output
is common with fractional scales because applications render at 1× and
are upscaled by the compositor.

---

### 1.2 Wayland

Wayland is the modern display protocol. Applications render their own surfaces,
but for **fractional** scaling the compositor still resamples in many setups.
In practice, clients often render at an integer buffer scale (e.g. 2x for a
1.25x output) and the compositor downsamples to the final output scale.

**Scaling on Wayland**

| Desktop | Mechanism |
|---|---|
| GNOME Shell (Mutter) | `org.gnome.desktop.interface scaling-factor` (integer) + `scale-monitor-framebuffer` experimental feature (fractional) |
| KDE Plasma (KWin) | Per-output scale via `kscreen-doctor` or system settings |
| Sway / wlroots | `output … scale` in `~/.config/sway/config` |

### 1.3 XWayland inside Wayland

XWayland is an X server running as a Wayland client. It allows legacy X11 apps
to run in Wayland sessions.

| App type | Rendering path | Typical scaling behavior |
|---|---|---|
| Native Wayland app | App buffer -> Wayland compositor -> scanout | Usually sharpest path; fractional may still require compositor resampling |
| X11 app via XWayland | X11 app -> XWayland surface -> Wayland compositor | Extra translation/compositing step; blur/overhead risk at fractional scales |

This is why a Wayland session can still show X11-like scaling artifacts for
some apps even when the desktop itself is Wayland-native.

---

### 1.4 Desktop Environments

| DE | Default display server | Scaling API |
|---|---|---|
| GNOME | Wayland (X11 fallback) | gsettings `scaling-factor` |
| KDE Plasma | Wayland (X11 fallback) | kscreen-doctor / kreadconfig5 |
| Xfce | X11 (Wayland experimental) | xfconf `Gdk/WindowScalingFactor` |
| LXQt | X11 | Qt env vars |
| MATE | X11 | gsettings (MATE fork) |

---

## 2. GPU Drivers on Linux

### Open-source drivers

| GPU vendor | Kernel driver | Mesa 3D component |
|---|---|---|
| Intel | `i915` / `xe` | Intel ANV (Vulkan), Iris (OpenGL) |
| AMD | `amdgpu` / `radeon` | RADV (Vulkan), RadeonSI (OpenGL) |
| NVIDIA | `nouveau` | NVK (Vulkan, experimental) |
| VMs | `virtio-gpu`, `qxl`, `vboxvideo` | virgl / llvmpipe |

### Proprietary drivers

- **NVIDIA** — `nvidia` kernel module + `libGL`/Vulkan libraries from the
  NVIDIA package.  Needed for full performance and proper Wayland support
  (GBM-based `nvidia-drm`).

### Software rendering

When no GPU driver is usable, Mesa falls back to:
- **llvmpipe** — LLVM-accelerated CPU rasteriser.
- **softpipe** — Reference software rasteriser.

Software rendering severely limits FPS and makes scaling even more expensive.

---

## 3. The `linux-desktop-analysis.py` Script

### Purpose

The script provides a single-command diagnostic for desktop scaling
performance.  It is designed to answer these questions:

1. **What scale is my desktop running at right now?**
2. **How many FPS is my compositor producing at that scale?**
3. **What happens to performance when I switch to a different scale?**
4. **Is my GPU driver appropriate for my hardware?**
5. **Are mouse movements likely to be smooth?**
6. **Is my RAM usage normal, and does scaling affect it?**
7. **Given my hardware, is this a reasonable setup?**
8. **Which render/scaling pipeline is active, and why is it efficient or not?**

### Workflow

```
┌─────────────────────────────────────────┐
│  1. Collect system info                 │
│     CPU · RAM · GPU · driver · DE       │
│     display server · desktop env        │
├─────────────────────────────────────────┤
│  2. Detect start scale                  │
│     gsettings / kscreen-doctor /        │
│     xfconf / xrandr                     │
├─────────────────────────────────────────┤
│  3. Immediate run at current scale      │
│     map to nearest required test case   │
├─────────────────────────────────────────┤
│  4. Run remaining required cases        │
│     1.0x, 2.0x, fractional (default 1.25x) │
├─────────────────────────────────────────┤
│  5. Auto scale switch + manual fallback │
│     for each missing case               │
├─────────────────────────────────────────┤
│  6. Optional mouse capture              │
│     only with --mouse-test              │
├─────────────────────────────────────────┤
│  7. Analyze render/scaling pipeline     │
│     Wayland/XWayland, driver path,      │
│     output topology, bottlenecks        │
├─────────────────────────────────────────┤
│  8. Determine compositor-present FPS    │
│     strategy + tool availability        │
├─────────────────────────────────────────┤
│  9. Assess driver suitability           │
│     Check for software rasteriser       │
│     Match driver to GPU vendor          │
├─────────────────────────────────────────┤
│ 10. Print consolidated report           │
│     test matrix · strategy · pipeline   │
└─────────────────────────────────────────┘
```

### Key Metrics Reported

| Metric | Why it matters |
|---|---|
| **Start scale** | Captures current environment without forcing an immediate switch |
| **Required test scales** | Ensures comparable matrix (`1.0`, `2.0`, fractional) |
| **Baseline FPS** | Reference rendering throughput before any change |
| **Per-case FPS** | Throughput/score for each matrix case |
| **FPS strategy** | How compositor-present FPS was estimated in this environment |
| **RAM used (baseline)** | Establishes memory baseline |
| **RAM delta** | > 200 MB increase suggests extra framebuffer allocations |
| **Mouse smoothness** | Wayland + libinput + high refresh rate = smooth |
| **Driver suitability** | Software renderer or wrong driver = poor performance |
| **Hardware profile** | Contextualises whether results are expected for the machine |
| **Pipeline analysis** | Explains active render/scaling path and likely bottlenecks |
| **Output topology** | Per-display mode/refresh/scale influence on scaling cost |

### What combinations the script handles

| Session | Native apps | XWayland apps | Coverage in script |
|---|---|---|---|
| X11 | Yes | n/a | Scale detection (`xrandr`), FPS, RAM, driver checks |
| Wayland | Yes | Optional | Scale detection (`wlr-randr`, `kscreen-doctor`, `gsettings`, etc.), XWayland client count (`xlsclients`), FPS, RAM, driver checks |

The script can detect XWayland presence and list clients, but it does not yet
separate FPS between native Wayland and XWayland-only workloads.

### Compositor-present FPS strategy (preferred over input-event sampling)

Desktop draw FPS should be measured from compositor/present timing, not from
mouse input event cadence. Mouse events only describe input stream timing and
can be misleading when used as a rendering proxy.

Recommended strategy hierarchy:

1. Use compositor-native frame/present telemetry when available.
2. Fallback to present/scanout-adjacent timing sources for that session type.
3. Always report output refresh and frame-time consistency (not only one FPS number).

| Environment | Primary strategy | Fallback strategy | Key tools |
|---|---|---|---|
| GNOME Wayland (Mutter) | Mutter/GNOME shell perf telemetry | Output refresh + benchmark score + frame-time proxy | `gdbus`, `wayland-info`, `xrandr` |
| KDE Wayland (KWin) | KWin telemetry/debug channels | Output refresh + benchmark score + frame-time proxy | `kscreen-doctor`, `qdbus`/`gdbus`, `xrandr` |
| COSMIC Wayland | `cosmic-comp` telemetry when available | Output topology + benchmark score + frame-time proxy | `wlr-randr`, `wayland-info`, `xrandr` |
| Sway/wlroots | wlroots compositor stats/debug signals | Output topology + benchmark score + frame-time proxy | `wlr-randr`, compositor-specific CLI |
| Hyprland | `hyprctl` runtime telemetry | Output refresh + benchmark score + frame-time proxy | `hyprctl`, `xrandr` |
| X11 desktops (Xfce/MATE/LXQt/i3/Openbox) | X compositor/present telemetry if available | Output refresh + benchmark score + frame-time proxy | `xrandr`, compositor-specific tools |

Minimum output fields for decision-quality diagnostics:

- active output refresh (`Hz`),
- selected strategy and data source,
- achieved FPS/score,
- frame-time stability indicators (avg/p95/p99 when available),
- context flags (Wayland/XWayland mix, driver path, fractional/integer scaling).

### Command-line Options

```
python3 linux-desktop-analysis.py [OPTIONS]

Options:
  --non-interactive    Skip all prompts (for scripting / automation)
  --fractional-scale FACTOR
                       Fractional case in the 3-run matrix (default 1.25)
  --scale FACTOR       Deprecated alias for --fractional-scale
  --mouse-test         Enable libinput event capture (disabled by default)
  --allow-glxgears-fallback
                       Use glxgears only if glmark2 is unavailable
  -h, --help           Show help and exit
```

### Examples

```bash
# Interactive mode (default)
python3 scripts/linux-desktop-analysis.py

# Test scale 2× non-interactively
python3 scripts/linux-desktop-analysis.py --fractional-scale 1.25 --non-interactive

# Test 1.5× (fractional)
python3 scripts/linux-desktop-analysis.py --fractional-scale 1.5

# Enable mouse event capture explicitly
python3 scripts/linux-desktop-analysis.py --mouse-test
```

### Dependencies

The script uses only Python standard-library modules plus common Linux
utilities that are typically pre-installed:

| Utility | Purpose | Package (Debian/Ubuntu) |
|---|---|---|
| `mangohud` | Preferred HUD wrapper for app FPS/frame-time overlays | `mangohud` |
| `glmark2` | Primary graphics benchmark | `glmark2` |
| `glxgears` | Last-resort fallback benchmark | `mesa-utils` |
| `glxinfo` | OpenGL renderer info | `mesa-utils` |
| `lspci` | GPU model & kernel driver | `pciutils` |
| `xrandr` | X11 scale detection & setting | `x11-xserver-utils` |
| `gsettings` | GNOME scale detection & setting | `libglib2.0-bin` |
| `kscreen-doctor` | KDE scale detection & setting | `kscreen` |
| `xfconf-query` | Xfce scale detection & setting | `xfconf` |
| `libinput` | Mouse input driver info | `libinput-tools` |
| `xinput` | X11 pointer device list | `xinput` |

Benchmark backend order (current implementation):

1. `mangohud + glmark2` (preferred),
2. `GALLIUM_HUD + glmark2` (Mesa fallback),
3. plain `glmark2`,
4. optional `glxgears` fallback only when enabled.

Notes:
- HUD backends improve frame-time visibility for the benchmarked app.
- They still do **not** directly measure global compositor present FPS for all desktop surfaces.
- Script output now includes a dedicated **Compositor Diagnostics** section with strategy-specific probes.

All utilities are optional; the script degrades gracefully when any of
them is absent.

---

## 4. Why This Matters

### HiDPI proliferation

As 4K and 5K screens become mainstream, 1× scaling makes UI elements
uncomfortably small.  Users commonly choose 1.5× or 2× scaling.
However, the overhead of different scaling implementations varies
significantly, and a bad configuration can cause stuttering, blurry text,
or excessive RAM consumption.

### Fractional scaling cost

Integer scaling (2x) is usually cheaper and cleaner: compositor/app can stay on
integer pixel grids with minimal resampling. Fractional scaling (e.g. 1.25x,
1.5x) often means one of these paths:

- client renders at an integer buffer scale above target (e.g. 2x), compositor downsamples;
- client renders lower, compositor upsamples;
- mixed path with extra intermediate buffers.

So there is no contradiction: Wayland improves ownership and timing of
rendering, but fractional scaling can still add GPU/memory cost because final
output scale is not an integer multiple.

### Display-specific scaling cost factors

Fractional scaling efficiency is not only a DE/compositor property. It also
depends on each active output:

- output resolution (e.g. 4K costs more than 1080p for the same scale factor),
- output refresh rate (e.g. 120 Hz doubles frame budget pressure vs 60 Hz),
- per-output scale mix (mixed DPI monitors increase composition complexity),
- active mode transitions (VRR/HDR/gamescope vs desktop compositor path).

This is why the script now reports output topology and pipeline class, not only
raw FPS deltas.

### Most efficient path for fractional scaling (practical)

For desktop usage, the strongest efficiency pattern is usually:

1. Native Wayland apps as primary workload (minimize XWayland clients).
2. Correct hardware driver stack (avoid software rendering; avoid nouveau when
  proprietary NVIDIA is needed for stable throughput on your card).
3. Keep compositor workload predictable (avoid heavy background render stress
  during diagnostics).
4. Prefer integer scaling if acceptable; when fractional is required, keep
  target close to practical readability needs (e.g. 1.25x instead of 1.5x when viable).
5. Validate at your real refresh rate and monitor layout, not only in a single
  synthetic benchmark.

For gaming-like workflows, gamescope-based pipelines can be more efficient than
full-desktop fractional scaling on weaker GPUs because internal render
resolution and upscale path are tuned differently.

### Driver correctness

A GPU running on the wrong driver (e.g. `nouveau` on a modern NVIDIA card,
or `llvmpipe` when hardware acceleration should be available) will produce
very low FPS at any scale.  Identifying this early prevents misattributing
a driver problem to a scaling problem.

### NVIDIA driver source model (repo vs distro)

Both models exist:

- **Distro-shipped packaging (recommended default)**:
  Ubuntu/Pop!_OS/Fedora/Arch/openSUSE provide NVIDIA packages in their own
  repos (sometimes with separate non-free/restricted channels). This is usually
  the safest maintenance path because kernel/ABI integration is handled by the distro.
- **NVIDIA-provided upstream repository**:
  NVIDIA also publishes its own package repositories for several distros.
  This can be useful for very new driver branches, but increases integration
  responsibility on your side.

Practical recommendation: start with distro packages unless you have a specific
branch/version requirement that the distro cannot provide.

### Mouse smoothness

Pointer latency is dominated by the display refresh rate and the input
pipeline (Wayland input vs X11 events), compositor load, and GPU driver state.
Sub-60 Hz is not automatically bad: 50-59 Hz can still feel fine. Severe issues
usually appear when refresh rate is very low or frame scheduling is unstable.

Important: mouse-event timing is not a valid substitute for compositor-present
FPS. Keep pointer tests optional and separate from rendering performance tests.

---

## 5. Bazzite Fractional Scaling Notes (2026)

Bazzite can feel very fast on older GPUs because of stack choices and defaults,
not because fractional scaling is free.

What helps in practice:

- Modern Fedora Atomic base images with frequent kernel/graphics updates.
- NVIDIA images with pre-integrated drivers for supported cards (avoids accidental nouveau usage on many installs).
- Steam Gaming Mode on deck/HTPC images uses gamescope, with frame cap and resolution scaling controls.
- Gaming Mode runs a minimal session, reducing background compositor/desktop overhead.

GT 1030 context:

- GT 1030 is Pascal-generation; Bazzite documents support for Pascal in its NVIDIA image track.
- This is a likely reason your GT 1030 experience is much better than nouveau-based stacks.

Important distinction:

- Desktop fractional scaling (KDE/GNOME Wayland compositor) and gamescope scaling are different pipelines.
- Gamescope workflows can render lower internal resolution and upscale, which often gives better smoothness on weak GPUs than full-desktop fractional scaling.

Diagnostic recommendation:

- Always label test mode as either desktop compositor scaling or gamescope scaling.
- Compare both before concluding that "fractional scaling" is efficient.

---

## 6. XFCE + Wayland + Fractional Scaling (2026 status)

Current upstream status indicates Xfce Wayland support is still experimental.
Xfce 4.20 can run on Wayland (typically via `labwc`), but feature parity with
X11 is incomplete and behavior depends on compositor protocol support.

Practical takeaway:

- If you need predictable fractional scaling today, GNOME/KDE Wayland are usually safer.
- Xfce Wayland can be tested, but expect gaps and compositor-specific behavior.
- Xubuntu exposing only 200% in UI is consistent with conservative integer-scaling defaults in some stacks.

---

## 7. Related Documentation

- `docs/SWAP_CONFIGURATIONS.md` — swap and memory tuning
- `scripts/debian-install/system_info.py` — headless system info module
- `scripts/debian-install/benchmark.py` — general performance benchmarks
