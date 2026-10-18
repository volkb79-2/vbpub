#!/bin/sh
# mdt-cockpit shell environment. SOURCED (never executed) by every shell family:
#   login shells        /etc/profile.d/50-mdt-cockpit.sh  (restores PATH first)
#   interactive bash    /etc/bash.bashrc  -> bashrc
#   interactive zsh     /etc/zsh/zshrc    -> zshrc
# It never edits PATH: the one definition lives in the Dockerfile (ENV PATH) and
# the login-shell hook merely puts that value back after Debian's /etc/profile
# replaced it. See docs/DESIGN-GUIDE.md "One PATH order".

# Sourced more than once per shell (login + interactive): do the work once.
if [ -n "${_mdt_profile_done:-}" ]; then
    return 0 2>/dev/null || true
fi
_mdt_profile_done=1

MDT_CUSTOMIZATION_ROOT=/usr/local/share/mdt-cockpit
export MDT_CUSTOMIZATION_ROOT
# The user's overrides. Nothing here exists until the user creates it; the image
# never writes into this directory.
MDT_USER_CONFIG="$HOME/.config/mdt-cockpit"

# Oh My Zsh (VS Code image family) is image-owned. ZSH is the only variable the
# framework needs to find itself; its cache goes to the home (see zshrc).
if [ -d /opt/mdt/oh-my-zsh ]; then
    ZSH=/opt/mdt/oh-my-zsh
    export ZSH
fi

# Image defaults first, then the user's files, so a user file can override or
# unset any default. ai.env has no image default: it carries the user's keys.
for bootstrap_file in \
    "$MDT_CUSTOMIZATION_ROOT/shell.env" \
    "$MDT_USER_CONFIG/shell.env" \
    "$MDT_USER_CONFIG/ai.env"
do
    if [ -r "$bootstrap_file" ]; then
        set -a
        # shellcheck source=/dev/null
        . "$bootstrap_file"
        set +a
    fi
done

# Cgroup governance banner — shows the EFFECTIVE limits this container is running
# under (host-controlled, cgroup v2), so you can see at a glance whether you're on
# a governed host. Placement itself is invisible/immutable from in here
# (cgroupns=private), but /sys/fs/cgroup maps straight onto this container's own
# leaf cgroup, so reading it needs no host path traversal. Silently prints nothing
# per line whose file is unreadable or empty (ungoverned host, or knob unset).
# See "Host resource governance (cgroups/slices)" in DEVCONTAINER-LIFECYCLE.md.
mdt_cgroup_banner_line() {
    # $1 = file under /sys/fs/cgroup, $2 = label to print
    _mdt_cg_file="/sys/fs/cgroup/$1"
    [ -r "$_mdt_cg_file" ] || return 0
    _mdt_cg_val=$(cat "$_mdt_cg_file" 2>/dev/null)
    [ -n "$_mdt_cg_val" ] || return 0
    printf '  cgroup %-11s %s\n' "$2:" "$_mdt_cg_val"
}

mdt_cgroup_banner() {
    mdt_cgroup_banner_line memory.max  memory.max
    mdt_cgroup_banner_line memory.high memory.high
    mdt_cgroup_banner_line cpu.weight  cpu.weight
    mdt_cgroup_banner_line io.max      io.max
}

# KSM opt-in banner — reports whether THIS shell actually opted into KSM at
# exec time. $MDT_KSM_STATUS is set by customization/ksm-optin.c's constructor,
# which runs (per ELF startup order) before this shell imports its environment,
# so the value is already visible here. Unset entirely means the shim was never
# preloaded (ENABLE_KSM_OPTIN=false at build time) — prints nothing then, same
# silent-when-inapplicable convention as the cgroup banner above.
mdt_ksm_banner() {
    case "$MDT_KSM_STATUS" in
        enabled)     printf '  ksm         opted-in (KSM_OPTIN_VERBOSE=1 for exec-level detail)\n' ;;
        unavailable) printf '  ksm         unavailable (kernel <6.4, CONFIG_KSM off, or missing CAP_SYS_RESOURCE)\n' ;;
    esac
}

case "$-" in
    *i*)
        for aliases_file in \
            "$MDT_CUSTOMIZATION_ROOT/aliases.sh" \
            "$MDT_USER_CONFIG/aliases.sh"
        do
            if [ -r "$aliases_file" ]; then
                # shellcheck source=/dev/null
                . "$aliases_file"
            fi
        done
        mdt_cgroup_banner
        mdt_ksm_banner
        ;;
esac
