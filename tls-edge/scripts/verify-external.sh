#!/usr/bin/env bash
# Probe one externally reachable HTTPS consumer from a client network.
# This is an opt-in live acceptance check, not part of the offline run-gate.
set -euo pipefail

usage() {
    echo "Usage: $0 <service-hostname> <expected-body-text>" >&2
    echo "Requires curl with HTTP/2 and HTTP/3 support; certificate checks stay enabled." >&2
    exit 2
}

[[ $# == 2 ]] || usage
host=$1
expected=$2
[[ $host =~ ^([[:alnum:]-]+\.)*[[:alnum:]-]+$ ]] || usage
[[ -n $expected ]] || usage

curl_version=$(curl -q --version)
if [[ $curl_version != *HTTP3* ]]; then
    echo "verify-external: curl does not include HTTP/3 support" >&2
    exit 2
fi

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT
url="https://$host/"

if ! h2_result=$(curl -q --http2 --fail --silent --show-error \
    --connect-timeout 15 --max-time 45 \
    --dump-header "$tmp_dir/h2.headers" --output "$tmp_dir/h2.body" \
    --write-out 'version=%{http_version} status=%{response_code} verify=%{ssl_verify_result}' \
    "$url"); then
    echo "verify-external: HTTP/2 request failed (TLS, routing, or HTTP status)" >&2
    exit 1
fi
[[ $h2_result == 'version=2 status=200 verify=0' ]] || {
    echo "verify-external: expected verified HTTP/2 over TCP; got: $h2_result" >&2
    exit 1
}
grep -Fq -- "$expected" "$tmp_dir/h2.body" || {
    echo "verify-external: HTTP/2 response did not contain the expected text" >&2
    exit 1
}
grep -Eiq '^alt-svc:[[:space:]]*h3=' "$tmp_dir/h2.headers" || {
    echo "verify-external: HTTP/2 response did not advertise HTTP/3 with Alt-Svc" >&2
    exit 1
}

if ! h3_result=$(curl -q --http3-only --fail --silent --show-error \
    --connect-timeout 15 --max-time 45 \
    --output "$tmp_dir/h3.body" \
    --write-out 'version=%{http_version} status=%{response_code} verify=%{ssl_verify_result}' \
    "$url"); then
    echo "verify-external: HTTP/3 request failed (TLS, routing, or UDP/443 reachability)" >&2
    exit 1
fi
[[ $h3_result == 'version=3 status=200 verify=0' ]] || {
    echo "verify-external: expected verified HTTP/3 over QUIC; got: $h3_result" >&2
    exit 1
}
grep -Fq -- "$expected" "$tmp_dir/h3.body" || {
    echo "verify-external: HTTP/3 response did not contain the expected text" >&2
    exit 1
}

printf '%s: %s; Alt-Svc advertises h3; %s\n' "$host" "$h2_result" "$h3_result"
