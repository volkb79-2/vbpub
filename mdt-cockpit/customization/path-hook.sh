# mdt-cockpit: restore the image's command search path in a LOGIN shell.
# POSIX sh; sourced by /etc/profile.d/50-mdt-cockpit.sh (bash, sh) and /etc/zsh/zprofile
# (zsh does not read /etc/profile on Debian).
#
# A login shell replaces PATH - Debian's /etc/profile for bash/sh, zsh's compiled-in default
# otherwise - and one that starts with a CLEARED environment (su -, sudo -i, ssh, cron,
# `env -i`) has no image ENV to inherit. The value the image was built with is therefore read
# from a file the build wrote (Dockerfile `ENV PATH`), and if that file is missing the hook
# says so once on stderr instead of silently leaving PATH wrong.
if [ -r /usr/local/share/mdt-cockpit/path ]; then
    PATH="$(cat /usr/local/share/mdt-cockpit/path)"
    export PATH
else
    echo "mdt-cockpit: /usr/local/share/mdt-cockpit/path is missing; PATH left as the login shell set it" >&2
fi
