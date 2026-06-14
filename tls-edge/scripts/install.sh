#!/usr/bin/env bash
# ─── scripts/install.sh — interactive tls-edge setup ───────────────────────
# Configures tls-edge for the current host: probes DNS, selects TLS mode,
# writes ciu-stack/ciu.toml.j2, and brings up the stack.
#
# Usage: scripts/install.sh [options]
#   --mode <acme-tls|acme-http|acme-dns|static|dev>
#   --domain <domain>   (repeatable)
#   --email <addr>
#   --fqdn <hostname>
#   --dns-provider <cloudflare|hetzner|route53|desec|other>
#   --staging           use Let's Encrypt staging CA
#   --enable-port-80
#   --ports <http>:<https>:<alt>   (e.g. 80:443:8443)
#   --cert-tool <mkcert|openssl>
#   --yes               non-interactive (accept all suggestions)
#   --render-only       write config + render templates, do not start stack
#   --no-up             write config + render templates, skip docker compose up
#   --help
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STACK_DIR="$REPO_ROOT/ciu-stack"
EDGE_DIR="$REPO_ROOT/edge-proxy"

ACME_STAGING_URL="https://acme-staging-v02.api.letsencrypt.org/directory"
ACME_PROD_URL="https://acme-v02.api.letsencrypt.org/directory"

# ─── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'
YEL='\033[0;33m'
GRN='\033[0;32m'
CYN='\033[0;36m'
BLD='\033[1m'
RST='\033[0m'

info()  { echo -e "${CYN}==>${RST} $*"; }
ok()    { echo -e "${GRN}  ✓${RST} $*"; }
warn()  { echo -e "${YEL}  ⚠${RST}  $*"; }
err()   { echo -e "${RED}  ✗${RST}  $*" >&2; }
fatal() { err "$*"; exit 1; }
hr()    { echo "──────────────────────────────────────────────────────────────────────────────"; }

print_banner() {
    hr
    echo -e "  ${BLD}tls-edge${RST}  ·  install.sh"
    hr
}

# ─── Argument defaults ────────────────────────────────────────────────────────
OPT_MODE=""
OPT_DOMAINS=()
OPT_EMAIL=""
OPT_FQDN=""
OPT_DNS_PROVIDER=""
OPT_STAGING=0
OPT_PORT80=0
OPT_PORTS=""
OPT_CERT_TOOL=""
OPT_YES=0
OPT_RENDER_ONLY=0
OPT_NO_UP=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --mode <m>         TLS mode: acme-tls | acme-http | acme-dns | static | dev
  --domain <d>       Domain (repeatable)
  --email <addr>     ACME contact email
  --fqdn <hostname>  Override detected FQDN
  --dns-provider <p> DNS provider: cloudflare | hetzner | route53 | desec | other
  --staging          Use Let's Encrypt staging CA
  --enable-port-80   Expose port 80 (HTTP -> HTTPS redirect / HTTP-01 challenges)
  --ports h:s:a      Custom port mapping http:https:https_alt (default 80:443:8443)
  --cert-tool <t>    Dev cert tool: mkcert | openssl
  --yes              Non-interactive; accept all suggestions
  --render-only      Write ciu.toml.j2 + render templates; do not start stack
  --no-up            Write + render; skip 'docker compose up'
  --help             Show this help
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)         OPT_MODE="$2"; shift 2 ;;
        --domain)       OPT_DOMAINS+=("$2"); shift 2 ;;
        --email)        OPT_EMAIL="$2"; shift 2 ;;
        --fqdn)         OPT_FQDN="$2"; shift 2 ;;
        --dns-provider) OPT_DNS_PROVIDER="$2"; shift 2 ;;
        --staging)      OPT_STAGING=1; shift ;;
        --enable-port-80) OPT_PORT80=1; shift ;;
        --ports)        OPT_PORTS="$2"; shift 2 ;;
        --cert-tool)    OPT_CERT_TOOL="$2"; shift 2 ;;
        --yes|-y)       OPT_YES=1; shift ;;
        --render-only)  OPT_RENDER_ONLY=1; shift ;;
        --no-up)        OPT_NO_UP=1; shift ;;
        --help|-h)      usage ;;
        *) fatal "Unknown option: $1" ;;
    esac
done

# ─── Prompt helper ────────────────────────────────────────────────────────────
# ask VAR "Prompt" "default"
ask() {
    local _var="$1" _prompt="$2" _default="${3:-}"
    if [[ $OPT_YES -eq 1 ]]; then
        printf -v "$_var" '%s' "$_default"
        return
    fi
    local _disp=""
    [[ -n "$_default" ]] && _disp=" [${_default}]"
    local _reply
    read -r -p "  ${_prompt}${_disp}: " _reply
    printf -v "$_var" '%s' "${_reply:-$_default}"
}

# ask_yn VAR "Prompt" default(y/n)
ask_yn() {
    local _var="$1" _prompt="$2" _default="${3:-n}"
    if [[ $OPT_YES -eq 1 ]]; then
        if [[ "$_default" =~ ^[Yy]$ ]]; then printf -v "$_var" '%s' "y"
        else printf -v "$_var" '%s' "n"; fi
        return
    fi
    local _opts="y/N"
    [[ "$_default" =~ ^[Yy]$ ]] && _opts="Y/n"
    local _reply
    read -r -p "  ${_prompt} [${_opts}]: " _reply
    _reply="${_reply:-$_default}"
    if [[ "$_reply" =~ ^[Yy]$ ]]; then printf -v "$_var" '%s' "y"
    else printf -v "$_var" '%s' "n"; fi
}

print_banner

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Preflight checks
# ═══════════════════════════════════════════════════════════════════════════════
info "Step 1/11: Preflight checks"

# Docker daemon
if ! docker info &>/dev/null; then
    fatal "docker daemon is not reachable (is Docker running?)"
fi
ok "Docker daemon reachable"

# docker compose (v2 plugin)
if ! docker compose version &>/dev/null; then
    fatal "docker compose (v2) is not available"
fi
ok "$(docker compose version --short 2>/dev/null | head -1 | sed 's/^/docker compose /')"

# Python + jinja2 + tomllib
if ! python3 -c 'import jinja2, tomllib' 2>/dev/null; then
    err "python3 with jinja2 is required"
    echo "  Debian/Ubuntu:  sudo apt install python3-jinja2" >&2
    echo "  pip:            pip install --user jinja2" >&2
    exit 3
fi
ok "python3 + jinja2 + tomllib available"

# ciu workspace detection: walk up from REPO_ROOT looking for ciu.global.defaults.toml.j2
CIU_WORKSPACE=0
_search_dir="$REPO_ROOT"
while [[ "$_search_dir" != "/" ]]; do
    if [[ -f "$_search_dir/ciu.global.defaults.toml.j2" ]]; then
        CIU_WORKSPACE=1
        CIU_ROOT="$_search_dir"
        break
    fi
    _search_dir="$(dirname "$_search_dir")"
done
if [[ $CIU_WORKSPACE -eq 1 ]]; then
    ok "ciu workspace detected: $CIU_ROOT"
else
    ok "Standalone mode (no ciu workspace found)"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — DooD probe
# ═══════════════════════════════════════════════════════════════════════════════
info "Step 2/11: DooD probe"
DOOD=0
if [[ -f "/.dockerenv" ]] && docker inspect "$(hostname)" &>/dev/null 2>&1; then
    DOOD=1
    warn "Running inside a container with Docker-out-of-Docker (DooD)"
else
    ok "Native host (not DooD)"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Host identity
# ═══════════════════════════════════════════════════════════════════════════════
info "Step 3/11: Host identity"

IPV4=""
IPV4="$(dig +short myip.opendns.com @resolver1.opendns.com 2>/dev/null | grep -E '^[0-9]+\.' | head -1)" || true
if [[ -z "$IPV4" ]]; then
    IPV4="$(curl -4fsS https://api.ipify.org 2>/dev/null)" || true
fi
if [[ -z "$IPV4" ]]; then
    warn "Could not detect public IPv4 address"
else
    ok "Public IPv4: $IPV4"
fi

IPV6=""
IPV6="$(curl -6fsS https://api64.ipify.org 2>/dev/null)" || true
# Filter out IPv4 responses from api64
if [[ "$IPV6" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    IPV6=""
fi
if [[ -n "$IPV6" ]]; then
    ok "Public IPv6: $IPV6"
fi

# PTR record
PTR=""
if [[ -n "$IPV4" ]]; then
    PTR="$(dig +short -x "$IPV4" 2>/dev/null | sed 's/\.$//' | head -1)" || true
fi

# Suggest FQDN
SUGGESTED_FQDN="${PTR:-$(hostname -f 2>/dev/null || hostname)}"

if [[ -n "$OPT_FQDN" ]]; then
    FQDN="$OPT_FQDN"
    ok "FQDN (from --fqdn): $FQDN"
else
    echo "  Detected PTR: ${PTR:-<none>}"
    echo "  Suggested FQDN: $SUGGESTED_FQDN"
    ask FQDN "Confirm/override FQDN" "$SUGGESTED_FQDN"
fi
ok "Using FQDN: $FQDN"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Domains
# ═══════════════════════════════════════════════════════════════════════════════
info "Step 4/11: Domains"

if [[ ${#OPT_DOMAINS[@]} -eq 0 ]]; then
    ask PRIMARY_DOMAIN "Primary domain" "$FQDN"
    OPT_DOMAINS=("$PRIMARY_DOMAIN")
    if [[ $OPT_YES -eq 0 ]]; then
        read -r -p "  Additional domains (space-separated, or Enter to skip): " _extra
        for _d in $_extra; do
            OPT_DOMAINS+=("$_d")
        done
    fi
else
    PRIMARY_DOMAIN="${OPT_DOMAINS[0]}"
fi

PRIMARY_DOMAIN="${OPT_DOMAINS[0]}"
echo "  Domains: ${OPT_DOMAINS[*]}"

# ACME email
ACME_EMAIL="$OPT_EMAIL"
if [[ -z "$ACME_EMAIL" && $OPT_YES -eq 0 ]]; then
    ask ACME_EMAIL "ACME contact email (recommended for renewal notices; Enter to skip)" ""
fi

# Wildcard cert?
WANT_WILDCARD="n"
ask_yn WANT_WILDCARD "Request wildcard certificate (covers *.$PRIMARY_DOMAIN)?" "n"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5 — DNS capability report
# ═══════════════════════════════════════════════════════════════════════════════
info "Step 5/11: DNS capability report"

DNS_OK=1          # at least A record matches for primary
HAS_WILDCARD_DNS=0

dns_check_domain() {
    local _dom="$1"
    local _a _aaaa
    _a="$(dig +short A "$_dom" 2>/dev/null | grep -E '^[0-9]+\.' | head -1)" || true
    _aaaa="$(dig +short AAAA "$_dom" 2>/dev/null | grep -E ':' | head -1)" || true

    echo "  Domain: $_dom"

    # A record vs IPv4
    if [[ -z "$_a" ]]; then
        warn "  A record: MISSING (issuance + routing will fail until DNS points here)"
        [[ "$_dom" == "$PRIMARY_DOMAIN" ]] && DNS_OK=0
    elif [[ -n "$IPV4" && "$_a" != "$IPV4" ]]; then
        warn "  A record: MISMATCH — DNS=$_a, this host=$IPV4 (routing + cert issuance will fail)"
        [[ "$_dom" == "$PRIMARY_DOMAIN" ]] && DNS_OK=0
    else
        ok "  A record: $_a  MATCH"
    fi

    # AAAA record
    if [[ -n "$_aaaa" ]]; then
        if [[ -z "$IPV6" ]]; then
            warn "  AAAA record: $_aaaa exists but this host has no public IPv6 — dangling record"
            warn "  Consider: set bind_ip=\"0.0.0.0\" in ciu.toml.j2 to force IPv4-only"
        else
            ok "  AAAA record: $_aaaa"
        fi
    elif [[ -n "$IPV6" ]]; then
        echo "  AAAA record: not set (host has IPv6 $IPV6; adding AAAA is optional)"
    fi
}

for _dom in "${OPT_DOMAINS[@]}"; do
    dns_check_domain "$_dom"
done

# Wildcard DNS probe
_probe="_tlsedge-probe-$RANDOM.$PRIMARY_DOMAIN"
_probe_a="$(dig +short A "$_probe" 2>/dev/null | grep -E '^[0-9]+\.' | head -1)" || true
if [[ -n "$_probe_a" ]]; then
    HAS_WILDCARD_DNS=1
    ok "Wildcard DNS present (*.$PRIMARY_DOMAIN → $_probe_a)"
fi

# Capability summary
echo
hr
echo "  DNS capability summary for $PRIMARY_DOMAIN:"
if [[ $HAS_WILDCARD_DNS -eq 1 ]]; then
    echo "  [✓] Subdomain self-service — wildcard DNS covers *.$PRIMARY_DOMAIN"
fi
echo "  [✓] Explicit per-subdomain records — add A records as you go"
if [[ "$WANT_WILDCARD" == "y" ]]; then
    echo "  [✓] Wildcard cert — requires acme-dns (automated DNS API) or CNAME delegation + manual certbot"
fi
echo "  [✓] Port-based fallback — https_alt :8443 always available"
hr

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6 — Mode selection
# ═══════════════════════════════════════════════════════════════════════════════
info "Step 6/11: TLS mode selection"

if [[ -n "$OPT_MODE" ]]; then
    SELECTED_MODE="$OPT_MODE"
    ok "Mode (from --mode): $SELECTED_MODE"
else
    # Suggest mode
    SUGGESTED_MODE="acme-tls"
    if [[ "$WANT_WILDCARD" == "y" ]]; then
        SUGGESTED_MODE="static"   # certbot --manual DNS-01 path; acme-dns needs a DNS provider API
    elif [[ -f "/etc/letsencrypt/live/$PRIMARY_DOMAIN/fullchain.pem" ]]; then
        SUGGESTED_MODE="static"
    elif [[ $DNS_OK -eq 0 ]]; then
        SUGGESTED_MODE="dev"
    fi

    echo "  Suggested mode: $SUGGESTED_MODE"
    echo
    echo "  Modes:"
    echo "    1) acme-tls  — Let's Encrypt via TLS-ALPN-01 (no port 80 needed) [default]"
    echo "    2) acme-http — Let's Encrypt via HTTP-01 (requires port 80)"
    echo "    3) acme-dns  — Let's Encrypt via DNS-01 (wildcard-capable; requires DNS provider API)"
    echo "    4) static    — pre-existing certs (certbot, corporate PKI, or manual wildcard TXT)"
    echo "    5) dev       — self-signed dev certs (no public DNS needed)"
    if [[ "$WANT_WILDCARD" == "y" ]]; then
        echo
        echo "  Wildcard cert options:"
        echo "    → acme-dns  (mode 3): automated DNS-01 via provider API — Traefik renews automatically"
        echo "    → static    (mode 4): certbot --manual DNS-01 — you add a TXT record interactively"
        echo "                          No DNS API access needed.  Requires manual action every ~60 days."
    fi

    _mode_default="$SUGGESTED_MODE"
    if [[ $OPT_YES -eq 1 ]]; then
        SELECTED_MODE="$_mode_default"
    else
        read -r -p "  Select mode [${_mode_default}]: " _mode_input
        case "${_mode_input:-$_mode_default}" in
            1|acme-tls)  SELECTED_MODE="acme-tls" ;;
            2|acme-http) SELECTED_MODE="acme-http" ;;
            3|acme-dns)  SELECTED_MODE="acme-dns" ;;
            4|static)    SELECTED_MODE="static" ;;
            5|dev)       SELECTED_MODE="dev" ;;
            *) SELECTED_MODE="${_mode_input:-$_mode_default}" ;;
        esac
    fi
    ok "Selected mode: $SELECTED_MODE"
fi

# Validate mode name
case "$SELECTED_MODE" in
    acme-tls|acme-http|acme-dns|static|dev) ;;
    *) fatal "Invalid mode '$SELECTED_MODE'; must be one of: acme-tls acme-http acme-dns static dev" ;;
esac

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 7 — Port 80
# ═══════════════════════════════════════════════════════════════════════════════
info "Step 7/11: Port 80 configuration"

EXPOSE_HTTP=false

if [[ $OPT_PORT80 -eq 1 ]]; then
    EXPOSE_HTTP=true
elif [[ "$SELECTED_MODE" == "acme-http" ]]; then
    warn "Mode acme-http requires port 80 — enabling automatically"
    EXPOSE_HTTP=true
else
    ask_yn _want80 "Expose port 80 (HTTP→HTTPS redirect)? (default: no)" "n"
    [[ "$_want80" == "y" ]] && EXPOSE_HTTP=true
fi

if [[ "$SELECTED_MODE" == "acme-http" && "$EXPOSE_HTTP" != "true" ]]; then
    fatal "Mode acme-http requires port 80 (pass --enable-port-80)"
fi

if [[ "$EXPOSE_HTTP" == "true" ]]; then
    # Check for existing :80 listener
    if ss -ltn 2>/dev/null | grep -q ':80 '; then
        warn "A process is already listening on port 80"
    fi
    ok "Port 80: enabled"
else
    ok "Port 80: disabled (TLS-only edge)"
fi

# Custom ports
PORT_HTTP=80
PORT_HTTPS=443
PORT_ALT=8443
BIND_IP=""

if [[ -n "$OPT_PORTS" ]]; then
    IFS=':' read -r PORT_HTTP PORT_HTTPS PORT_ALT <<<"$OPT_PORTS"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 8 — Mode-specific configuration
# ═══════════════════════════════════════════════════════════════════════════════
info "Step 8/11: Mode-specific configuration"

CA_SERVER="$ACME_PROD_URL"
USE_STAGING=0
DNS_PROVIDER=""
WILDCARD_MAIN=""
STATIC_CERT_BASE="/etc/letsencrypt"
STATIC_SOURCE="bind"
CERT_TOOL=""

case "$SELECTED_MODE" in

  # ── acme-tls / acme-http ──────────────────────────────────────────────────
  acme-tls|acme-http)
    if [[ $OPT_STAGING -eq 1 ]]; then
        USE_STAGING=1
    elif [[ $OPT_YES -eq 0 ]]; then
        ask_yn _stg "Use Let's Encrypt STAGING CA? (recommended for first test; certs untrusted)" "n"
        [[ "$_stg" == "y" ]] && USE_STAGING=1
    fi
    if [[ $USE_STAGING -eq 1 ]]; then
        CA_SERVER="$ACME_STAGING_URL"
        warn "Staging CA selected — certificates will NOT be trusted by browsers"
        warn "Re-run without --staging once you confirm issuance works"
    else
        ok "Production Let's Encrypt CA"
    fi
    ;;

  # ── acme-dns ──────────────────────────────────────────────────────────────
  acme-dns)
    if [[ $OPT_STAGING -eq 1 ]]; then
        USE_STAGING=1
        CA_SERVER="$ACME_STAGING_URL"
        warn "Staging CA selected"
    elif [[ $OPT_YES -eq 0 ]]; then
        ask_yn _stg "Use Let's Encrypt STAGING CA?" "n"
        [[ "$_stg" == "y" ]] && USE_STAGING=1 && CA_SERVER="$ACME_STAGING_URL"
    fi

    # DNS provider selection
    if [[ -n "$OPT_DNS_PROVIDER" ]]; then
        DNS_PROVIDER="$OPT_DNS_PROVIDER"
    else
        echo "  DNS providers:"
        echo "    1) cloudflare"
        echo "    2) hetzner"
        echo "    3) route53 (AWS)"
        echo "    4) desec (free; supports CNAME delegation)"
        echo "    5) other (enter lego provider code)"
        if [[ $OPT_YES -eq 1 ]]; then
            DNS_PROVIDER="cloudflare"
        else
            read -r -p "  Select provider [cloudflare]: " _prov_input
            case "${_prov_input:-1}" in
                1|cloudflare) DNS_PROVIDER="cloudflare" ;;
                2|hetzner)    DNS_PROVIDER="hetzner" ;;
                3|route53)    DNS_PROVIDER="route53" ;;
                4|desec)      DNS_PROVIDER="desec" ;;
                5|other)
                    ask DNS_PROVIDER "Enter lego provider code" "cloudflare"
                    ;;
                *)  DNS_PROVIDER="${_prov_input}" ;;
            esac
        fi
    fi
    ok "DNS provider: $DNS_PROVIDER"

    # Wildcard domain
    if [[ "$WANT_WILDCARD" == "y" ]]; then
        WILDCARD_MAIN="$PRIMARY_DOMAIN"
    fi

    # CNAME delegation instructions
    echo
    echo "  CNAME delegation (optional, for automated renewal without API access):"
    echo "    _acme-challenge.$PRIMARY_DOMAIN  CNAME  _acme-challenge.<api-zone>"
    echo "  See: https://www.eff.org/deeplinks/2018/02/technical-deep-dive-securing-automation-acme-dns-challenge-validation"
    echo

    # Credential prompts
    ENV_FILE="$EDGE_DIR/.env"
    ENV_CONTENT="# tls-edge acme-dns credentials — gitignored; chmod 600\n"

    case "$DNS_PROVIDER" in
        cloudflare)
            echo "  Cloudflare: create a scoped API token with Zone:Read + DNS:Edit"
            if [[ $OPT_YES -eq 0 ]]; then
                ask _cf_token "CF_DNS_API_TOKEN" ""
            else
                _cf_token=""
            fi
            [[ -n "$_cf_token" ]] && ENV_CONTENT+="CF_DNS_API_TOKEN=$_cf_token\n"
            ;;
        hetzner)
            if [[ $OPT_YES -eq 0 ]]; then
                ask _hz_key "HETZNER_API_KEY" ""
            else
                _hz_key=""
            fi
            [[ -n "$_hz_key" ]] && ENV_CONTENT+="HETZNER_API_KEY=$_hz_key\n"
            ;;
        route53)
            if [[ $OPT_YES -eq 0 ]]; then
                ask _aws_id  "AWS_ACCESS_KEY_ID" ""
                ask _aws_sec "AWS_SECRET_ACCESS_KEY" ""
                ask _aws_reg "AWS_REGION" "us-east-1"
            else
                _aws_id="" _aws_sec="" _aws_reg="us-east-1"
            fi
            [[ -n "$_aws_id" ]]  && ENV_CONTENT+="AWS_ACCESS_KEY_ID=$_aws_id\n"
            [[ -n "$_aws_sec" ]] && ENV_CONTENT+="AWS_SECRET_ACCESS_KEY=$_aws_sec\n"
            [[ -n "$_aws_reg" ]] && ENV_CONTENT+="AWS_REGION=$_aws_reg\n"
            ;;
        desec)
            if [[ $OPT_YES -eq 0 ]]; then
                ask _desec "DESEC_TOKEN" ""
            else
                _desec=""
            fi
            [[ -n "$_desec" ]] && ENV_CONTENT+="DESEC_TOKEN=$_desec\n"
            ;;
        *)
            echo "  Enter credentials as KEY=VALUE pairs (one per line; blank line to finish):"
            if [[ $OPT_YES -eq 0 ]]; then
                while true; do
                    read -r -p "  > " _kv
                    [[ -z "$_kv" ]] && break
                    ENV_CONTENT+="$_kv\n"
                done
            fi
            ;;
    esac

    # Write .env
    if [[ -f "$ENV_FILE" ]]; then
        cp "$ENV_FILE" "$ENV_FILE.bak"
        ok "Backed up existing .env → .env.bak"
    fi
    printf '%b' "$ENV_CONTENT" >"$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok "Wrote credentials: $ENV_FILE (chmod 600)"
    ;;

  # ── static ────────────────────────────────────────────────────────────────
  static)
    ask STATIC_CERT_BASE "Certificate base directory (parent of live/ and archive/)" "/etc/letsencrypt"

    for _dom in "${OPT_DOMAINS[@]}"; do
        echo "  Probing certificates for $_dom..."
        if ! docker run --rm \
              -v "${STATIC_CERT_BASE}:/c:ro" \
              alpine:3.20 \
              sh -c "cat /c/live/$_dom/fullchain.pem >/dev/null" 2>/dev/null; then
            warn "Certificate not readable for $_dom in $STATIC_CERT_BASE"
            echo "  This may be a broken symlink or a permissions issue."
            ask_yn _fixperms "Attempt to fix certificate permissions (chgrp docker + chmod g+rX)?" "y"
            if [[ "$_fixperms" == "y" ]]; then
                _sudo=""
                [[ $EUID -ne 0 ]] && _sudo="sudo"
                $_sudo chgrp -R docker "$STATIC_CERT_BASE/live" "$STATIC_CERT_BASE/archive" 2>/dev/null || true
                $_sudo chmod -R g+rX  "$STATIC_CERT_BASE/live" "$STATIC_CERT_BASE/archive" 2>/dev/null || true
                # privkey needs g+r specifically
                if [[ -f "$STATIC_CERT_BASE/archive/$_dom/privkey1.pem" ]]; then
                    $_sudo chmod g+r "$STATIC_CERT_BASE/archive/$_dom/privkey"*.pem 2>/dev/null || true
                fi
                ok "Permissions adjusted; re-probing..."
                if ! docker run --rm \
                      -v "${STATIC_CERT_BASE}:/c:ro" \
                      alpine:3.20 \
                      sh -c "cat /c/live/$_dom/fullchain.pem >/dev/null" 2>/dev/null; then
                    warn "Certificate still not readable — continuing anyway; fix before starting stack"
                else
                    ok "Certificate readable: $STATIC_CERT_BASE/live/$_dom/fullchain.pem"
                fi
            fi
        else
            ok "Certificate readable: $STATIC_CERT_BASE/live/$_dom/fullchain.pem"
        fi
    done

    # Wildcard cert: offer to issue via certbot --manual --preferred-challenges dns
    if [[ "$WANT_WILDCARD" == "y" ]]; then
        _wc_cert="$STATIC_CERT_BASE/live/$PRIMARY_DOMAIN/fullchain.pem"
        if [[ -f "$_wc_cert" ]]; then
            ok "Wildcard certificate already present: $_wc_cert"
        else
            warn "No wildcard certificate found at $_wc_cert"
            echo
            echo "  Manual wildcard TXT flow (no DNS API required):"
            echo "    1. certbot prompts you to add a TXT record:  _acme-challenge.$PRIMARY_DOMAIN"
            echo "    2. Add the record in your DNS zone control panel"
            echo "    3. Press Enter in certbot to complete validation"
            echo "    4. Cert is saved to $STATIC_CERT_BASE/live/$PRIMARY_DOMAIN/"
            echo
            echo "  IMPORTANT — renewal:"
            echo "    certbot --manual certs do NOT renew automatically."
            echo "    Every ~60 days you must re-run:"
            echo "      certbot certonly --manual --preferred-challenges dns \\"
            echo "        -d $PRIMARY_DOMAIN -d '*.$PRIMARY_DOMAIN'"
            echo "    Set a calendar reminder ~2 weeks before expiry."
            echo "    Alternative: use acme-dns mode if you ever gain DNS API access."
            echo
            ask_yn _run_certbot "Run certbot --manual for wildcard certificate now?" "y"
            if [[ "$_run_certbot" == "y" ]]; then
                if ! command -v certbot &>/dev/null; then
                    warn "certbot is not installed"
                    echo "  Install: sudo apt install certbot"
                    echo "  Then run manually:"
                    echo "    certbot certonly --manual --preferred-challenges dns \\"
                    if [[ -n "$ACME_EMAIL" ]]; then
                        echo "      --email '$ACME_EMAIL' --agree-tos \\"
                    else
                        echo "      --register-unsafely-without-email --agree-tos \\"
                    fi
                    echo "      -d '$PRIMARY_DOMAIN' -d '*.$PRIMARY_DOMAIN'"
                else
                    echo
                    info "Running certbot --manual for $PRIMARY_DOMAIN and *.$PRIMARY_DOMAIN ..."
                    echo "  Watch for the TXT record prompt.  certbot will pause and wait for you."
                    echo
                    _certbot_email_args=()
                    if [[ -n "$ACME_EMAIL" ]]; then
                        _certbot_email_args=(--email "$ACME_EMAIL" --agree-tos --no-eff-email)
                    else
                        _certbot_email_args=(--register-unsafely-without-email --agree-tos)
                    fi
                    certbot certonly \
                        --manual \
                        --preferred-challenges dns \
                        "${_certbot_email_args[@]}" \
                        -d "$PRIMARY_DOMAIN" \
                        -d "*.$PRIMARY_DOMAIN"
                    ok "Wildcard certificate issued: $STATIC_CERT_BASE/live/$PRIMARY_DOMAIN/"
                fi
            fi
        fi
    fi

    # Install certbot deploy hook
    HOOK_SRC="$SCRIPT_DIR/certbot-deploy-hook.sh"
    HOOK_DST="/etc/letsencrypt/renewal-hooks/deploy/01-reload-traefik.sh"
    if [[ -f "$HOOK_SRC" ]]; then
        _sudo=""
        [[ $EUID -ne 0 ]] && _sudo="sudo"
        if $_sudo cp "$HOOK_SRC" "$HOOK_DST" 2>/dev/null; then
            # Replace placeholder with actual edge-proxy path
            $_sudo sed -i "s|__EDGE_PROXY_DIR__|$EDGE_DIR|g" "$HOOK_DST"
            $_sudo chmod +x "$HOOK_DST"
            ok "Certbot deploy hook installed: $HOOK_DST"
            echo "  Verify renewal: sudo certbot renew --dry-run"
        else
            warn "Could not install deploy hook (need sudo?); manual step:"
            echo "  sudo cp $HOOK_SRC $HOOK_DST"
            echo "  sudo sed -i 's|__EDGE_PROXY_DIR__|$EDGE_DIR|g' $HOOK_DST"
            echo "  sudo chmod +x $HOOK_DST"
        fi
    else
        warn "certbot-deploy-hook.sh not found in scripts/ — skipping hook installation"
    fi
    ;;

  # ── dev ───────────────────────────────────────────────────────────────────
  dev)
    # Select tool
    CERT_TOOL="$OPT_CERT_TOOL"
    if [[ -z "$CERT_TOOL" ]]; then
        if command -v mkcert &>/dev/null; then
            CERT_TOOL="mkcert"
        else
            CERT_TOOL="openssl"
        fi
    fi
    ok "Dev cert tool: $CERT_TOOL"

    # Target: volume (DooD) or dir
    DEV_TARGET="dir"
    [[ $DOOD -eq 1 ]] && DEV_TARGET="volume"

    DEV_OUT="$REPO_ROOT/certs-dev"

    # Build --domain args
    _dom_args=()
    for _d in "${OPT_DOMAINS[@]}"; do
        _dom_args+=(--domain "$_d")
    done

    "$SCRIPT_DIR/dev-certs.sh" \
        "${_dom_args[@]}" \
        --tool "$CERT_TOOL" \
        --target "$DEV_TARGET" \
        --out "$DEV_OUT"

    STATIC_SOURCE="$DEV_TARGET"  # "dir" or "volume"
    if [[ "$DEV_TARGET" == "dir" ]]; then
        STATIC_CERT_BASE="$DEV_OUT"
    fi

    ok "Dev certificates generated"
    ;;
esac

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 9 — Write ciu-stack/ciu.toml.j2
# ═══════════════════════════════════════════════════════════════════════════════
info "Step 9/11: Writing ciu-stack/ciu.toml.j2"

TOML_FILE="$STACK_DIR/ciu.toml.j2"

# Backup existing
if [[ -f "$TOML_FILE" ]]; then
    cp "$TOML_FILE" "$TOML_FILE.bak"
    ok "Backed up existing ciu.toml.j2 → ciu.toml.j2.bak"
fi

# Build TOML content — only keys differing from defaults
{
    echo "# ─── tls-edge override configuration — generated by install.sh ──────────────"
    echo "# Edit by hand or re-run scripts/install.sh.  Do not edit ciu.defaults.toml.j2."
    echo "# Defaults: tls.mode=acme-tls, ports.expose_http=false, cert_base=/etc/letsencrypt"
    echo

    # [tls_edge.ports] — only emit keys that differ from defaults
    _ports_section=""
    [[ "$EXPOSE_HTTP" == "true" ]] && _ports_section+="expose_http = true\n"
    [[ "$PORT_HTTP"  != "80"   ]] && _ports_section+="http = $PORT_HTTP\n"
    [[ "$PORT_HTTPS" != "443"  ]] && _ports_section+="https = $PORT_HTTPS\n"
    [[ "$PORT_ALT"   != "8443" ]] && _ports_section+="https_alt = $PORT_ALT\n"
    [[ -n "$BIND_IP"           ]] && _ports_section+="bind_ip = \"$BIND_IP\"\n"
    if [[ -n "$_ports_section" ]]; then
        echo "[tls_edge.ports]"
        printf '%b' "$_ports_section"
        echo
    fi

    # [tls_edge.tls]
    echo "[tls_edge.tls]"
    echo "mode = \"$SELECTED_MODE\""
    echo

    # [tls_edge.acme]
    case "$SELECTED_MODE" in
        acme-tls|acme-http|acme-dns)
            echo "[tls_edge.acme]"
            [[ -n "$ACME_EMAIL" ]] && echo "email = \"$ACME_EMAIL\""
            [[ "$CA_SERVER" != "$ACME_PROD_URL" ]] && echo "ca_server = \"$CA_SERVER\""
            echo
            ;;
    esac

    # [tls_edge.acme.dns]
    if [[ "$SELECTED_MODE" == "acme-dns" ]]; then
        echo "[tls_edge.acme.dns]"
        echo "provider = \"$DNS_PROVIDER\""
        [[ -n "$WILDCARD_MAIN" ]] && echo "wildcard_main = \"$WILDCARD_MAIN\""
        echo
    fi

    # [tls_edge.static]
    case "$SELECTED_MODE" in
        static|dev)
            echo "[tls_edge.static]"
            [[ "$STATIC_CERT_BASE" != "/etc/letsencrypt" ]] && echo "cert_base = \"$STATIC_CERT_BASE\""
            # domains array
            printf 'domains = ['
            for i in "${!OPT_DOMAINS[@]}"; do
                [[ $i -gt 0 ]] && printf ', '
                printf '"%s"' "${OPT_DOMAINS[$i]}"
            done
            printf ']\n'
            [[ "$STATIC_SOURCE" != "bind" ]] && echo "source = \"$STATIC_SOURCE\""
            echo
            ;;
    esac

} >"$TOML_FILE"

ok "Written: $TOML_FILE"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 10 — ciu workspace: print instructions and exit
# ═══════════════════════════════════════════════════════════════════════════════
info "Step 10/11: ciu workspace check"

if [[ $CIU_WORKSPACE -eq 1 ]]; then
    echo
    ok "This repo is ciu-managed — run 'ciu' inside ciu-stack/ to deploy"
    echo
    if [[ "$SELECTED_MODE" == "acme-dns" ]]; then
        echo "  For acme-dns in ciu mode, add to ciu.toml.j2 instead of using .env:"
        echo
        echo "    [tls_edge.secrets]"
        case "$DNS_PROVIDER" in
            cloudflare) echo "    dns_api_token = \"ASK_EXTERNAL:CF_DNS_API_TOKEN\"" ;;
            hetzner)    echo "    dns_api_key   = \"ASK_EXTERNAL:HETZNER_API_KEY\"" ;;
            route53)
                echo "    aws_access_key_id     = \"ASK_EXTERNAL:AWS_ACCESS_KEY_ID\""
                echo "    aws_secret_access_key = \"ASK_EXTERNAL:AWS_SECRET_ACCESS_KEY\""
                ;;
            desec)      echo "    dns_api_token = \"ASK_EXTERNAL:DESEC_TOKEN\"" ;;
            *)          echo "    dns_api_token = \"ASK_EXTERNAL:<YOUR_PROVIDER_TOKEN_VAR>\"" ;;
        esac
        echo
    fi
    exit 0
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 10 (standalone) — Render templates
# ═══════════════════════════════════════════════════════════════════════════════

echo "  Rendering templates..."
"$SCRIPT_DIR/render.sh"
ok "Templates rendered"

# Validate compose
echo "  Validating docker-compose.yml..."
if ! docker compose -f "$EDGE_DIR/docker-compose.yml" -f /dev/null config -q --project-directory "$EDGE_DIR" 2>/dev/null; then
    # Try without extra -f
    (cd "$EDGE_DIR" && docker compose config -q)
fi
ok "docker compose config: valid"

if [[ $OPT_RENDER_ONLY -eq 1 ]]; then
    ok "--render-only: stopping before docker compose up"
    exit 0
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 11 — Start stack + summary
# ═══════════════════════════════════════════════════════════════════════════════
info "Step 11/11: Starting stack"

if [[ $OPT_NO_UP -eq 1 ]]; then
    ok "--no-up: skipping docker compose up"
else
    echo "  Running: docker compose up -d --wait"
    (cd "$EDGE_DIR" && docker compose up -d --wait)
    ok "Stack is up"

    # Run verify script if present
    VERIFY_SCRIPT="$SCRIPT_DIR/verify.sh"
    if [[ -f "$VERIFY_SCRIPT" ]]; then
        echo "  Running: scripts/verify.sh"
        bash "$VERIFY_SCRIPT"
    fi
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
echo
hr
echo -e "  ${BLD}Setup complete${RST}"
hr
echo "  Mode:    $SELECTED_MODE"
echo "  Domains: ${OPT_DOMAINS[*]}"
echo
echo "  URLs:"
for _dom in "${OPT_DOMAINS[@]}"; do
    echo "    https://$_dom"
    [[ "$PORT_ALT" != "443" ]] && echo "    https://$_dom:$PORT_ALT  (alt port)"
done
echo

if [[ $USE_STAGING -eq 1 ]]; then
    warn "STAGING certificates: not trusted by browsers"
    echo "    Re-run without --staging once issuance is confirmed"
    echo
fi

echo "  Renewal:"
case "$SELECTED_MODE" in
    acme-tls|acme-http)
        echo "    Automatic — Traefik renews via ACME ${SELECTED_MODE#acme-}"
        ;;
    acme-dns)
        echo "    Automatic — Traefik renews via DNS-01 (provider: $DNS_PROVIDER)"
        ;;
    static)
        if [[ "$WANT_WILDCARD" == "y" ]]; then
            echo "    Manual wildcard — certbot --manual does NOT auto-renew."
            echo "    Every ~60 days, re-run:"
            echo "      certbot certonly --manual --preferred-challenges dns \\"
            if [[ -n "$ACME_EMAIL" ]]; then
                echo "        --email '$ACME_EMAIL' --agree-tos --no-eff-email \\"
            else
                echo "        --register-unsafely-without-email --agree-tos \\"
            fi
            echo "        -d '$PRIMARY_DOMAIN' -d '*.$PRIMARY_DOMAIN'"
            echo "    Then add the new TXT record and press Enter."
            echo "    The deploy hook reloads Traefik automatically after renewal."
            echo "    Set a calendar reminder ~2 weeks before expiry:"
            echo "      certbot certificates  (check expiry dates)"
        else
            echo "    Automatic certbot renewal — deploy hook re-renders conf.d/certs.yml"
            echo "    Test:  sudo certbot renew --dry-run"
        fi
        ;;
    dev)
        echo "    Manual — re-run scripts/dev-certs.sh to regenerate (certs valid 397 days)"
        ;;
esac

echo
echo "  DNS capability:"
[[ $HAS_WILDCARD_DNS -eq 1 ]] && echo "    Wildcard DNS (*.$PRIMARY_DOMAIN) — subdomain self-service enabled"
[[ $DNS_OK -eq 1 ]] && echo "    A record for $PRIMARY_DOMAIN points to this host" || echo "    WARNING: A record for $PRIMARY_DOMAIN is missing or mismatched"

echo
echo "  See also: CONSUMER_GUIDE.md"
hr
