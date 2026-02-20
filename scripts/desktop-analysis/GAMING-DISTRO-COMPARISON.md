# Gaming-Focused Distro Comparison (DistroWatch snapshot)

This document compares five gaming-oriented distributions requested for explicit support in the desktop analysis tooling:
- Bazzite
- Pop!_OS
- Regata OS
- Nobara Project
- Garuda Linux

Data source: DistroWatch project pages and release/package tables.

## Scope and data caveats

- DistroWatch reader-comment **full text** was not accessible in this environment due consent/cookie interstitial pages.
- This comparison therefore uses available public metadata from DistroWatch pages:
  - latest listed release/version and date,
  - distro summary text,
  - release/headline snippets,
  - ratings/popularity counters.

## Feature comparison table

| Distro | DistroWatch page | Home page | Latest listed version (DistroWatch) | Base | Release model | Desktop focus | Gaming/system optimization signals |
|---|---|---|---|---|---|---|---|
| Bazzite | https://distrowatch.com/table.php?distribution=bazzite | https://bazzite.gg/ | 43.20260217 (2026-02-17) | Fedora | Fixed (image-based immutable) | GNOME, KDE Plasma | Immutable/read-only base; Flatpak-centric workflow; targets Steam Deck/handhelds; gaming-focused packaging and image variants |
| Pop!_OS | https://distrowatch.com/table.php?distribution=popos | https://system76.com/pop | 24.04 (2025-12-11) | Ubuntu LTS / Debian family | Fixed | COSMIC, GNOME | Hardware-vendor integration focus; COSMIC desktop transition; broad laptop/desktop support with developer/gaming-friendly defaults |
| Regata OS | https://distrowatch.com/table.php?distribution=regata | https://www.regataos.com.br/ | 25.1.0 (2026-02-02) | openSUSE | Fixed | KDE Plasma | Explicit “gaming mode” via Vulkan API mention; hybrid graphics configuration support; integrated game portal/store |
| Nobara Project | https://distrowatch.com/table.php?distribution=nobara | https://nobaraproject.org/ | 43 (2025-12-27) | Fedora | Rolling (current table) | GNOME, KDE Plasma | Ships gaming/streaming-oriented defaults: NVIDIA drivers, Wine dependencies, OBS, codec stack and Fedora usability fixes |
| Garuda Linux | https://distrowatch.com/table.php?distribution=garuda | https://garudalinux.org/ | 260115 (2026-01-15) | Arch | Rolling | Multi-edition (KDE, GNOME, Hyprland, Sway, i3, Xfce, etc.) | Performance-focused defaults: zram, performance CPU governor, custom memory-management tooling, Timeshift rollback/stability workflow |

## Latest community signals (available metadata)

Because direct comment bodies were unavailable in this environment, these are “community signals” from visible DistroWatch metadata:

- **Bazzite**: strong gaming identity; handheld/Steam Deck positioning; high visitor rating shown on page.
- **Pop!_OS**: high popularity ranking; significant attention around COSMIC era releases.
- **Regata OS**: niche but explicit gaming messaging (Vulkan game mode, hybrid graphics support).
- **Nobara**: steady gaming audience; out-of-box gaming and creator tooling emphasized.
- **Garuda**: performance-tweak identity clearly communicated; broad desktop edition matrix for gamer preference.

## Kernel and system optimization notes for gaming operation

Cross-distro patterns relevant to gaming performance and latency:

- **Kernel flavor and cadence**:
  - Fedora/Arch-derived gaming distros typically track newer kernels faster.
  - Ubuntu-LTS-derived systems trade some bleeding-edge gains for stability.
- **GPU driver strategy**:
  - Pre-integrated NVIDIA drivers and codec stacks reduce setup friction (notably Nobara).
  - Mesa cadence and Vulkan stack freshness are central for AMD/Intel performance.
- **Memory and scheduler behavior**:
  - zram and tuned memory behavior (explicitly called out by Garuda) can improve responsiveness under load.
  - CPU governor defaults (performance vs. schedutil/powersave) materially affect frame-time stability.
- **Rollback and update resilience**:
  - Immutable/image-based approaches (Bazzite) and snapshot workflows (Garuda/Timeshift) reduce breakage risk after updates.
- **Wayland/XWayland maturity by desktop**:
  - Gaming overlays/capture paths still vary by compositor and session mode; distro defaults influence out-of-box success.

## Script adaptation review and decisions

### Implemented now

- Added explicit distro-family detection support in [scripts/desktop-analysis/desktop-analysis.py](../scripts/desktop-analysis/desktop-analysis.py):
  - `garuda` -> `arch`
  - `regata` -> `suse`
  - Existing explicit mappings already present:
    - `pop` -> `ubuntu`
    - `bazzite`, `nobara` -> `fedora`

### Adaptations now implemented in script

1. **Gaming optimization checks section**
  - Implemented structured checks for:
    - zram enabled and swap-device usage details,
    - CPU governor and platform profile,
    - gamemode process/service state,
    - gamescope/steam runtime activity,
    - gaming tool binary availability,
    - kernel flavor tags (`zen`, `xanmod`, `liquorix`, `bore`, etc.).

2. **Immutable vs mutable operational hints**
  - Implemented operational guidance that distinguishes immutable/image-based workflows from mutable package-manager workflows.

3. **Distro-profile-specific package probes**
  - Implemented profile-aware package probing for gaming stack packages:
    - Fedora-like (Nobara/Bazzite),
    - Arch-like (Garuda),
    - Ubuntu-like (Pop!_OS),
    - openSUSE-like (Regata),
    - Debian fallback profile.

4. **Desktop/compositor gaming compatibility hints**
  - Implemented compositor-aware hints in pipeline analysis for Hyprland/Sway/Wayfire/COSMIC/KWin Wayland and mixed Wayland+XWayland paths.

## References

- Bazzite: https://distrowatch.com/table.php?distribution=bazzite
- Pop!_OS: https://distrowatch.com/table.php?distribution=popos
- Regata OS: https://distrowatch.com/table.php?distribution=regata
- Nobara Project: https://distrowatch.com/table.php?distribution=nobara
- Garuda Linux: https://distrowatch.com/table.php?distribution=garuda
