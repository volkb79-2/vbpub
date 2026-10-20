#!/usr/bin/env bash
# Positive identification of the harness guest. Sourced by every guest script
# BEFORE it changes anything. A VM check is not enough: gstammtisch (production)
# is itself a KVM guest, so `systemd-detect-virt` says "kvm" there too.
#
# The marker is /etc/zwg-harness-guest containing exactly
#     zwg-harness-guest run=<run> nonce=<nonce>
# It is written only by run-synthetic.sh, over ssh, into a guest it has just
# started with vmctl, and the same <run> and <nonce> are handed to each guest
# script in the environment (ZWG_RUN, ZWG_NONCE). Nothing creates it on any
# other machine. Anyone who copies these scripts to a production host must
# create the file by hand, which is a deliberate act, not an accident.
#
# Also refused, independently of the marker: any active swap area that is not
# the guest's own /swapfile (a production swap partition is never touched, and
# `swapoff -a` is never used).

zwg_refuse() {
    echo "zwg-guard: REFUSING: $*" >&2
    exit 97
}

require_harness_guest() {
    local marker=/etc/zwg-harness-guest
    [ -n "${ZWG_RUN:-}" ] && [ -n "${ZWG_NONCE:-}" ] \
        || zwg_refuse "ZWG_RUN/ZWG_NONCE not set; these scripts are started only by run-synthetic.sh"
    [ -f "$marker" ] || zwg_refuse "$marker missing: this is not a guest started by run-synthetic.sh"
    [ "$(stat -c %U "$marker")" = root ] || zwg_refuse "$marker is not owned by root"
    [ "$(cat "$marker")" = "zwg-harness-guest run=$ZWG_RUN nonce=$ZWG_NONCE" ] \
        || zwg_refuse "$marker content does not match ZWG_RUN/ZWG_NONCE"
    zwg_require_only_own_swap
}

zwg_require_only_own_swap() {
    local bad
    bad="$(awk 'NR>1 && $1 != "/swapfile" {print $1}' /proc/swaps)"
    [ -z "$bad" ] || zwg_refuse "active swap area(s) other than /swapfile: $bad"
}
