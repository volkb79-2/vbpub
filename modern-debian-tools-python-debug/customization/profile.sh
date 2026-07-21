#!/bin/sh
MDT_CUSTOMIZATION_ROOT=/usr/local/share/modern-debian-tools-python-debug
export MDT_CUSTOMIZATION_ROOT
export PATH="$HOME/.local/bin:$PATH"

for bootstrap_file in \
    "$HOME/.config/modern-debian-tools-python-debug/shell.env" \
    "$HOME/.config/modern-debian-tools-python-debug/ai.env"
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
        if [ -r "$HOME/.config/modern-debian-tools-python-debug/aliases.sh" ]; then
            # shellcheck source=/dev/null
            . "$HOME/.config/modern-debian-tools-python-debug/aliases.sh"
        fi
        mdt_cgroup_banner
        mdt_ksm_banner
        ;;
esac
