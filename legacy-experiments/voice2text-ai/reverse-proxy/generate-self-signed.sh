#!/bin/sh
# Generate self-signed TLS certificates for development/testing
# This script runs automatically if Let's Encrypt certificates are not available

set -e

CERT_DIR="/etc/nginx/certs"
CA_KEY="$CERT_DIR/ca-key.pem"
CA_CERT="$CERT_DIR/ca-cert.pem"
SERVER_KEY="$CERT_DIR/privkey.pem"
SERVER_CERT="$CERT_DIR/fullchain.pem"

# Skip if certificates already exist
if [ -f "$SERVER_CERT" ] && [ -f "$SERVER_KEY" ]; then
    echo "✓ TLS certificates already exist, skipping generation"
    exit 0
fi

echo "⚠ No Let's Encrypt certificates found, generating self-signed certificates..."

# Get public IP and FQDN
PUBLIC_IP=$(wget -qO- https://api.ipify.org || echo "127.0.0.1")
PUBLIC_FQDN=${PUBLIC_FQDN:-localhost}

# Generate CA if it doesn't exist
if [ ! -f "$CA_CERT" ]; then
    echo "Generating Certificate Authority..."
    openssl genrsa -out "$CA_KEY" 4096 2>/dev/null
    openssl req -x509 -new -nodes -key "$CA_KEY" \
        -sha256 -days 3650 -out "$CA_CERT" \
        -subj "/C=US/ST=State/L=City/O=Voice2Text-AI/CN=Voice2Text-AI Root CA" 2>/dev/null
    echo "✓ CA certificate generated: $CA_CERT"
fi

# Generate server certificate
echo "Generating server certificate for $PUBLIC_FQDN ($PUBLIC_IP)..."

# Generate server key
openssl genrsa -out "$SERVER_KEY" 2048 2>/dev/null

# Create certificate signing request
openssl req -new -key "$SERVER_KEY" -out /tmp/server.csr \
    -subj "/C=US/ST=State/L=City/O=Voice2Text-AI/CN=$PUBLIC_FQDN" 2>/dev/null

# Create certificate with SAN
cat > /tmp/openssl.cnf <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
[req_distinguished_name]
[v3_req]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation, digitalSignature, keyEncipherment
subjectAltName = @alt_names
[alt_names]
DNS.1 = $PUBLIC_FQDN
DNS.2 = localhost
IP.1 = $PUBLIC_IP
IP.2 = 127.0.0.1
EOF

# Sign certificate with CA
openssl x509 -req -in /tmp/server.csr -CA "$CA_CERT" -CAkey "$CA_KEY" \
    -CAcreateserial -out "$SERVER_CERT" -days 365 -sha256 \
    -extfile /tmp/openssl.cnf -extensions v3_req 2>/dev/null

# Cleanup
rm -f /tmp/server.csr /tmp/openssl.cnf

echo "✓ Self-signed certificates generated successfully"
echo "  CA Certificate: $CA_CERT"
echo "  Server Certificate: $SERVER_CERT"
echo "  Server Key: $SERVER_KEY"
echo ""
echo "⚠ To avoid browser warnings, install the CA certificate:"
echo "  docker run --rm -v voice2text-proxy-certs:/certs alpine cat /certs/ca-cert.pem > ca-cert.pem"
echo "  Then install ca-cert.pem in your browser/system trust store"
