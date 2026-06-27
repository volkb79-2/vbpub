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

case "$-" in
    *i*)
        if [ -r "$HOME/.config/modern-debian-tools-python-debug/aliases.sh" ]; then
            # shellcheck source=/dev/null
            . "$HOME/.config/modern-debian-tools-python-debug/aliases.sh"
        fi
        ;;
esac
