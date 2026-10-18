# mdt-cockpit: put the image's managed PATH entries in order in a LOGIN shell.
# POSIX sh (no word splitting, no globbing: it also runs under zsh); sourced by
# /etc/profile.d/50-mdt-cockpit.sh (bash, sh) and /etc/zsh/zprofile (zsh does not read /etc/profile).
#
# /etc/profile keeps an inherited PATH (the image patches Debian's unconditional assignments to
# `${PATH:-default}`), so entries added at runtime - a consumer containerEnv.PATH, Dev Containers
# features, the userEnvProbe login shell - are still there. This hook MERGES: it removes the
# managed entries wherever they occur, prepends the managed prefix (D5 order), appends the managed
# suffix, and keeps every other entry in its relative order. Sourcing it twice changes nothing.
#
# The managed entries are written at build time (line 1: prefix, line 2: suffix) because a login
# shell that starts with a CLEARED environment (su -, sudo -i, ssh, cron, env -i) has no image ENV.
# If the file is missing the hook says so once on stderr instead of silently leaving PATH wrong.
_mdt_entries=/usr/local/share/mdt-cockpit/path-entries
if [ -r "$_mdt_entries" ]; then
    { IFS= read -r _mdt_prefix; IFS= read -r _mdt_suffix; } < "$_mdt_entries"
    _mdt_managed=":${_mdt_prefix}:${_mdt_suffix}:"
    _mdt_rest=$PATH
    _mdt_kept=
    while [ -n "$_mdt_rest" ]; do
        case $_mdt_rest in
            *:*) _mdt_dir=${_mdt_rest%%:*}; _mdt_rest=${_mdt_rest#*:} ;;
            *)   _mdt_dir=$_mdt_rest; _mdt_rest= ;;
        esac
        [ -n "$_mdt_dir" ] || continue
        case $_mdt_managed in
            *":${_mdt_dir}:"*) ;;
            *) _mdt_kept=${_mdt_kept:+${_mdt_kept}:}${_mdt_dir} ;;
        esac
    done
    PATH=${_mdt_prefix}${_mdt_kept:+:${_mdt_kept}}${_mdt_suffix:+:${_mdt_suffix}}
    export PATH
    unset _mdt_prefix _mdt_suffix _mdt_managed _mdt_rest _mdt_kept _mdt_dir
else
    echo "mdt-cockpit: ${_mdt_entries} is missing; PATH left as the login shell set it" >&2
fi
unset _mdt_entries
