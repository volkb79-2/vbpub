#!/usr/bin/env bash
# ─── scripts/dev-certs.sh — generate dev TLS certificates ──────────────────
# Generates self-signed certificates for local development using mkcert or
# openssl.  Optionally loads the result into a Docker volume for DooD use.
#
# Usage: scripts/dev-certs.sh --domain <d> [--domain <d2>] \
#          [--tool mkcert|openssl] [--target dir|volume] [--out <dir>]
set -euo pipefail

# ─── Banner ──────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

print_banner() {
    echo "──────────────────────────────────────────────────────────────────────────────"
    echo "  tls-edge  ·  dev-certs.sh"
    echo "──────────────────────────────────────────────────────────────────────────────"
}

usage() {
    cat <<EOF
Usage: $0 --domain <domain> [options]

Generate self-signed development certificates for tls-edge.

Options:
  --domain <d>           Domain to generate a cert for (repeatable, required)
  --tool mkcert|openssl  Certificate tool (default: mkcert if installed, else openssl)
  --target dir|volume    Where to write certs: local dir or Docker volume (default: dir)
  --out <dir>            Output directory (default: <repo>/certs-dev)
  --help                 Show this help

Output layout:
  <out>/live/<domain>/fullchain.pem
  <out>/live/<domain>/privkey.pem
  <out>/archive/             (empty dir required as mount point)

For --target volume: copies certs into Docker volume 'edge-proxy_certs-dev'.
EOF
    exit 0
}

# ─── Argument parsing ─────────────────────────────────────────────────────────
DOMAINS=()
TOOL=""
TARGET="dir"
OUT_DIR="$REPO_ROOT/certs-dev"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)    DOMAINS+=("$2"); shift 2 ;;
        --tool)      TOOL="$2"; shift 2 ;;
        --target)    TARGET="$2"; shift 2 ;;
        --out)       OUT_DIR="$2"; shift 2 ;;
        --help|-h)   usage ;;
        *) echo "error: unknown option: $1" >&2; exit 1 ;;
    esac
done

# ─── Validation ───────────────────────────────────────────────────────────────
if [[ ${#DOMAINS[@]} -eq 0 ]]; then
    echo "error: at least one --domain is required" >&2
    exit 1
fi

if [[ "$TARGET" != "dir" && "$TARGET" != "volume" ]]; then
    echo "error: --target must be 'dir' or 'volume'" >&2
    exit 1
fi

# Resolve tool
if [[ -z "$TOOL" ]]; then
    if command -v mkcert &>/dev/null; then
        TOOL="mkcert"
    else
        TOOL="openssl"
    fi
fi

if [[ "$TOOL" != "mkcert" && "$TOOL" != "openssl" ]]; then
    echo "error: --tool must be 'mkcert' or 'openssl'" >&2
    exit 1
fi

if [[ "$TOOL" == "mkcert" ]] && ! command -v mkcert &>/dev/null; then
    echo "error: mkcert is not installed; use --tool openssl or install mkcert" >&2
    exit 1
fi

print_banner
echo "  Tool:    $TOOL"
echo "  Domains: ${DOMAINS[*]}"
echo "  Target:  $TARGET"
echo "  Out dir: $OUT_DIR"
echo "──────────────────────────────────────────────────────────────────────────────"
echo

# ─── Create directory layout ──────────────────────────────────────────────────
mkdir -p "$OUT_DIR/archive"

# ─── openssl: one-time CA ─────────────────────────────────────────────────────
CA_DIR="$OUT_DIR/ca"
if [[ "$TOOL" == "openssl" ]]; then
    if [[ ! -f "$CA_DIR/ca.pem" ]]; then
        echo "==> Creating dev CA in $CA_DIR"
        mkdir -p "$CA_DIR"
        openssl genrsa -out "$CA_DIR/ca.key" 4096
        chmod 600 "$CA_DIR/ca.key"
        openssl req -new -x509 \
            -key "$CA_DIR/ca.key" \
            -out "$CA_DIR/ca.pem" \
            -days 3650 \
            -subj "/CN=tls-edge dev CA/O=tls-edge/C=DE"
        echo "    CA created: $CA_DIR/ca.pem"
    else
        echo "==> Reusing existing dev CA: $CA_DIR/ca.pem"
    fi
fi

# ─── Generate per-domain certificates ────────────────────────────────────────
for DOMAIN in "${DOMAINS[@]}"; do
    CERT_DIR="$OUT_DIR/live/$DOMAIN"
    mkdir -p "$CERT_DIR"

    echo "==> Generating certificate for $DOMAIN"

    if [[ "$TOOL" == "mkcert" ]]; then
        mkcert \
            -cert-file "$CERT_DIR/fullchain.pem" \
            -key-file  "$CERT_DIR/privkey.pem" \
            "$DOMAIN" "*.$DOMAIN"
        chmod 600 "$CERT_DIR/privkey.pem"
        echo "    Certificate: $CERT_DIR/fullchain.pem"
        echo "    Private key: $CERT_DIR/privkey.pem"

    else
        # openssl: generate key + CSR + leaf signed with SANs
        KEY="$CERT_DIR/privkey.pem"
        CSR="$CERT_DIR/csr.pem"
        LEAF="$CERT_DIR/leaf.pem"
        EXT_FILE="$CERT_DIR/san.cnf"

        openssl genrsa -out "$KEY" 2048
        chmod 600 "$KEY"

        cat >"$EXT_FILE" <<EXTEOF
[req]
distinguished_name = dn
req_extensions = v3_req
prompt = no

[dn]
CN = $DOMAIN

[v3_req]
subjectAltName = DNS:$DOMAIN,DNS:*.$DOMAIN
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
EXTEOF

        openssl req -new \
            -key "$KEY" \
            -out "$CSR" \
            -subj "/CN=$DOMAIN" \
            -config "$EXT_FILE"

        cat >"$CERT_DIR/v3ext.cnf" <<EXTEOF2
subjectAltName = DNS:$DOMAIN,DNS:*.$DOMAIN
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
EXTEOF2

        openssl x509 -req \
            -in "$CSR" \
            -CA "$CA_DIR/ca.pem" \
            -CAkey "$CA_DIR/ca.key" \
            -CAcreateserial \
            -out "$LEAF" \
            -days 397 \
            -extfile "$CERT_DIR/v3ext.cnf"

        # fullchain = leaf + CA
        cat "$LEAF" "$CA_DIR/ca.pem" >"$CERT_DIR/fullchain.pem"

        # Clean up scratch files
        rm -f "$CSR" "$LEAF" "$EXT_FILE" "$CERT_DIR/v3ext.cnf"

        echo "    Certificate: $CERT_DIR/fullchain.pem"
        echo "    Private key: $CERT_DIR/privkey.pem"
    fi
done

echo

# ─── Trust instructions ───────────────────────────────────────────────────────
if [[ "$TOOL" == "openssl" ]]; then
    echo "──────────────────────────────────────────────────────────────────────────────"
    echo "  Dev CA: $CA_DIR/ca.pem"
    echo
    echo "  Trust the CA in curl:    curl --cacert $CA_DIR/ca.pem https://..."
    echo "  Trust in browser/OS:     import $CA_DIR/ca.pem into your trust store"
    echo "    Linux (ca-certificates): sudo cp $CA_DIR/ca.pem /usr/local/share/ca-certificates/tls-edge-dev.crt"
    echo "                             sudo update-ca-certificates"
    echo "    macOS:                   sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain $CA_DIR/ca.pem"
    echo "──────────────────────────────────────────────────────────────────────────────"
else
    echo "──────────────────────────────────────────────────────────────────────────────"
    echo "  Certificates signed by your mkcert local CA."
    echo "  If you have not trusted the mkcert CA yet, run: mkcert -install"
    echo "  (Note: install.sh does not run this automatically.)"
    MKCERT_CA="$(mkcert -CAROOT 2>/dev/null)/rootCA.pem" || true
    if [[ -n "$MKCERT_CA" && -f "$MKCERT_CA" ]]; then
        echo "  CA location: $MKCERT_CA"
        echo "  curl:        curl --cacert $MKCERT_CA https://..."
    fi
    echo "──────────────────────────────────────────────────────────────────────────────"
fi
echo

# ─── Load into Docker volume (--target volume) ────────────────────────────────
if [[ "$TARGET" == "volume" ]]; then
    VOLUME_NAME="edge-proxy_certs-dev"
    echo "==> Loading certs into Docker volume '$VOLUME_NAME'"

    # Create volume if it does not exist
    if ! docker volume inspect "$VOLUME_NAME" &>/dev/null; then
        docker volume create "$VOLUME_NAME"
        echo "    Created volume: $VOLUME_NAME"
    else
        echo "    Reusing existing volume: $VOLUME_NAME"
    fi

    # Tar the live/ and archive/ trees into the volume.  chown to root: the
    # hardened traefik container runs as root with cap_drop ALL (no
    # CAP_DAC_OVERRIDE), so it can only read 0600 keys it owns.
    tar -C "$OUT_DIR" -c live archive \
      | docker run --rm -i \
          -v "$VOLUME_NAME:/certs" \
          alpine:3.20 \
          sh -c 'tar -x -C /certs && chown -R 0:0 /certs'
    echo "    Certs loaded into volume: $VOLUME_NAME"
    echo
    echo "  Use in ciu.toml.j2:"
    echo "    [tls_edge.static]"
    echo "    source = \"volume\""
else
    # DEV ONLY: the hardened traefik container runs as root with cap_drop ALL
    # (no CAP_DAC_OVERRIDE) and cannot read 0600 keys owned by another user.
    # These are throwaway self-signed dev keys — make them world-readable.
    find "$OUT_DIR/live" -name privkey.pem -exec chmod 644 {} +
    echo "  NOTE: dev private keys are chmod 644 so the hardened container can"
    echo "        read them — acceptable for self-signed dev certs only."
    echo
    echo "  Hint: add to ciu-stack/ciu.toml.j2:"
    echo "    [tls_edge.static]"
    echo "    cert_base = \"$OUT_DIR\""
    echo "    domains   = [\"${DOMAINS[0]}\"]"
    echo "    source    = \"bind\""
fi

echo
echo "  Done."
