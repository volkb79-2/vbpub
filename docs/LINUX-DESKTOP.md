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

Wayland is the modern display protocol.  Each application renders its own
surface at the advertised scale and hands pixel-perfect buffers to the
compositor.  This gives crisper fractional scaling than X11.

**Scaling on Wayland**

| Desktop | Mechanism |
|---|---|
| GNOME Shell (Mutter) | `org.gnome.desktop.interface scaling-factor` (integer) + `scale-monitor-framebuffer` experimental feature (fractional) |
| KDE Plasma (KWin) | Per-output scale via `kscreen-doctor` or system settings |
| Sway / wlroots | `output … scale` in `~/.config/sway/config` |

---

### 1.3 Desktop Environments

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

### Workflow

```
┌─────────────────────────────────────────┐
│  1. Collect system info                 │
│     CPU · RAM · GPU · driver · DE       │
│     display server · desktop env        │
├─────────────────────────────────────────┤
│  2. Detect baseline scale               │
│     gsettings / kscreen-doctor /        │
│     xfconf / xrandr                     │
├─────────────────────────────────────────┤
│  3. Measure baseline FPS & RAM          │
│     glxgears for ~5 s                   │
├─────────────────────────────────────────┤
│  4. Prompt for target scale             │
│     (or --scale FACTOR CLI arg)         │
├─────────────────────────────────────────┤
│  5. Apply target scale                  │
│     gsettings / kscreen-doctor /        │
│     xfconf / xrandr                     │
├─────────────────────────────────────────┤
│  6. Measure target FPS & RAM            │
├─────────────────────────────────────────┤
│  7. Restore baseline scale              │
├─────────────────────────────────────────┤
│  8. Assess mouse smoothness             │
│     Wayland vs X11 · libinput           │
│     refresh rate                        │
├─────────────────────────────────────────┤
│  9. Assess driver suitability           │
│     Check for software rasteriser       │
│     Match driver to GPU vendor          │
├─────────────────────────────────────────┤
│ 10. Print consolidated report           │
│     FPS comparison · RAM delta ·        │
│     efficiency verdict                  │
└─────────────────────────────────────────┘
```

### Key Metrics Reported

| Metric | Why it matters |
|---|---|
| **Baseline scale** | Confirms what the compositor is actually using |
| **Target scale** | The scale being evaluated |
| **Baseline FPS** | Reference rendering throughput before any change |
| **Target FPS** | Throughput at the new scale; large drops signal inefficiency |
| **FPS ratio** | target / baseline; ≥ 0.80 is considered acceptable |
| **RAM used (baseline)** | Establishes memory baseline |
| **RAM delta** | > 200 MB increase suggests extra framebuffer allocations |
| **Mouse smoothness** | Wayland + libinput + high refresh rate = smooth |
| **Driver suitability** | Software renderer or wrong driver = poor performance |
| **Hardware profile** | Contextualises whether results are expected for the machine |

### Command-line Options

```
python3 linux-desktop-analysis.py [OPTIONS]

Options:
  --non-interactive    Skip all prompts (for scripting / automation)
  --scale FACTOR       Target scale factor to test (e.g. 1.5, 2)
  -h, --help           Show help and exit
```

### Examples

```bash
# Interactive mode (default)
python3 scripts/linux-desktop-analysis.py

# Test scale 2× non-interactively
python3 scripts/linux-desktop-analysis.py --scale 2 --non-interactive

# Test 1.5× (fractional)
python3 scripts/linux-desktop-analysis.py --scale 1.5
```

### Dependencies

The script uses only Python standard-library modules plus common Linux
utilities that are typically pre-installed:

| Utility | Purpose | Package (Debian/Ubuntu) |
|---|---|---|
| `glxgears` | FPS benchmark | `mesa-utils` |
| `glxinfo` | OpenGL renderer info | `mesa-utils` |
| `lspci` | GPU model & kernel driver | `pciutils` |
| `xrandr` | X11 scale detection & setting | `x11-xserver-utils` |
| `gsettings` | GNOME scale detection & setting | `libglib2.0-bin` |
| `kscreen-doctor` | KDE scale detection & setting | `kscreen` |
| `xfconf-query` | Xfce scale detection & setting | `xfconf` |
| `libinput` | Mouse input driver info | `libinput-tools` |
| `xinput` | X11 pointer device list | `xinput` |

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

Integer scaling (2×) is handled efficiently: each surface buffer is
exactly four times the pixel count, and the compositor simply copies
it.  Fractional scaling (e.g. 1.5×) requires the application to render
at 2× and the compositor to downscale — or the application renders at 3×
and the compositor upscales — doubling or tripling buffer memory and GPU
fill-rate.

### Driver correctness

A GPU running on the wrong driver (e.g. `nouveau` on a modern NVIDIA card,
or `llvmpipe` when hardware acceleration should be available) will produce
very low FPS at any scale.  Identifying this early prevents misattributing
a driver problem to a scaling problem.

### Mouse smoothness

Pointer latency is dominated by the display refresh rate and the input
pipeline (Wayland input vs X11 events).  Low refresh rates (< 60 Hz)
cause visible cursor lag regardless of scale.

---

## 5. Related Documentation

- `docs/SWAP_CONFIGURATIONS.md` — swap and memory tuning
- `scripts/debian-install/system_info.py` — headless system info module
- `scripts/debian-install/benchmark.py` — general performance benchmarks
