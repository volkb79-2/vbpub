if command -v batcat >/dev/null 2>&1; then alias bat='batcat --paging=never'; fi
alias ll='ls -l'
alias la='ls -la'
alias gs='git status -sb'
alias gl='git log --oneline --decorate -n 20'
alias gd='git diff'
alias dps='docker ps --format "table {{.Names}}\\t{{.Status}}\\t{{.Ports}}"'

# zswap visibility helper.
zswap_status() {
    if [ -r /sys/module/zswap/parameters/enabled ]; then
        printf 'enabled=%s\n' "$(cat /sys/module/zswap/parameters/enabled)"
    fi
    if [ -r /sys/kernel/debug/zswap/stored_pages ]; then
        for file in /sys/kernel/debug/zswap/*; do
            [ -f "$file" ] || continue
            printf '%s=%s\n' "${file##*/}" "$(cat "$file")"
        done
        return 0
    fi
    if [ -r /sys/fs/cgroup/memory.zswap.current ]; then
        printf 'memory.zswap.current=%s\n' "$(cat /sys/fs/cgroup/memory.zswap.current)"
    fi
}
alias zswap-status='zswap_status'

# Yolo-mode examples: uncomment and fill in each tool's approval/safety flag.
# alias aider-yolo='aider --yes-always'
# alias claude-yolo='claude <approval-flags>'
# alias codex-yolo='codex <approval-flags>'
# alias reasonix-yolo='reasonix <approval-flags>'
# alias openclaw-yolo='openclaw <approval-flags>'
