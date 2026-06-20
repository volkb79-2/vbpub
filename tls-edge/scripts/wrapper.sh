#!/usr/bin/env bash
# /usr/local/bin/tls-edge — installed by get.py; do not edit by hand
# To update: tls-edge update
TLS_EDGE_HOME=/opt/tls-edge-src/tls-edge

_need_home() {
    if [[ ! -d "$TLS_EDGE_HOME" ]]; then
        echo "error: tls-edge not found at $TLS_EDGE_HOME" >&2
        echo "  Re-run the bootstrap installer to repair:" >&2
        echo "    curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/tls-edge/get.py | sudo python3 - install" >&2
        exit 1
    fi
}

case "${1:-}" in
    install)   _need_home; shift; exec bash "$TLS_EDGE_HOME/scripts/install.sh" "$@" ;;
    render)    _need_home; shift; exec bash "$TLS_EDGE_HOME/scripts/render.sh" "$@" ;;
    verify)    _need_home; shift; exec bash "$TLS_EDGE_HOME/scripts/verify.sh" "$@" ;;
    dev-certs) _need_home; shift; exec bash "$TLS_EDGE_HOME/scripts/dev-certs.sh" "$@" ;;
    update)    _need_home; exec python3 "$TLS_EDGE_HOME/get.py" update ;;
    version)
        _need_home
        git -C "$TLS_EDGE_HOME" describe --tags --match 'tls-edge-v*' 2>/dev/null \
            || cat "$TLS_EDGE_HOME/VERSION" 2>/dev/null \
            || echo "(unknown)"
        ;;
    status)
        _need_home
        docker compose -f "$TLS_EDGE_HOME/edge-proxy/docker-compose.yml" ps
        ;;
    help|--help|-h|'')
        cat <<'EOF'
Usage: tls-edge <command> [options]

Commands:
  install    Guided interactive setup
  render     Re-render templates from ciu.toml.j2 (after config changes)
  verify     Post-install verification checks
  dev-certs  Generate self-signed certs for dev mode
  update     Download and apply the latest tls-edge release
  version    Print installed version
  status     Show edge-proxy stack status
  help       Show this message

Run 'tls-edge install --help' for installer options.
EOF
        ;;
    *)
        echo "error: unknown command '$1'. Run 'tls-edge help' for usage." >&2
        exit 1
        ;;
esac
