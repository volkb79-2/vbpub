variable "REGISTRY" {
  default = "ghcr.io"
}

variable "NAMESPACE" {
  default = "volkb79-2"
}

// One version is released only when npm, PyPI, and MCR all provide it.
variable "PLAYWRIGHT_VERSION" {
  default = "1.64.0"
}

variable "PLAYWRIGHT_DISTRO" {
  default = "noble"
}

// Multi-arch manifest digest for the selected Playwright base image.
// The resolver refreshes this together with PLAYWRIGHT_VERSION.
variable "PLAYWRIGHT_IMAGE_DIGEST" {
  default = "sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f"
}

// @playwright/mcp pin (bundled MCP Streamable HTTP server).
variable "PLAYWRIGHT_MCP_VERSION" {
  default = "0.0.80"
}

// chrome-devtools-mcp pin (stdio-only CDP MCP server).
variable "CHROME_DEVTOOLS_MCP_VERSION" {
  default = "1.8.0"
}

// mcp-proxy pin (stdio→streamable-HTTP proxy for chrome-devtools-mcp).
variable "MCP_PROXY_VERSION" {
  default = "6.7.14"
}

// lighthouse pin (Node API for programmatic audits).
// Used by the vendored in-repo lighthouse-mcp server.
variable "LIGHTHOUSE_VERSION" {
  default = "13.4.1"
}

// CMRU's prepared, Playwright-driven pwmcp release coordinate.
variable "PWMCP_VERSION" {
  default = "1.64.0-r3"
}

// OCR sidecar base (containers/ocr/Dockerfile): debian:<release>-slim pinned by manifest digest.
// Projections of the same values live in that Dockerfile and cmru.vars; the resolver keeps them equal.
variable "DEBIAN_RELEASE" {
  default = "trixie"
}

variable "DEBIAN_IMAGE_DIGEST" {
  default = "sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad"
}

variable "OCI_SOURCE" {
  default = "https://github.com/volkb79-2/vbpub"
}

variable "OCI_DOCUMENTATION" {
  default = "https://github.com/volkb79-2/vbpub/tree/main/pwmcp"
}

// One coordinated build serves both Python and npm consumers.  The resolver only
// selects a version published by npm, PyPI, and the official MCR base image.
target "pwmcp-latest" {
  context    = "."
  dockerfile = "containers/pwmcp/Dockerfile"
  args = {
    PLAYWRIGHT_VERSION          = "${PLAYWRIGHT_VERSION}"
    PLAYWRIGHT_DISTRO           = "${PLAYWRIGHT_DISTRO}"
    PLAYWRIGHT_IMAGE_DIGEST     = "${PLAYWRIGHT_IMAGE_DIGEST}"
    PLAYWRIGHT_MCP_VERSION      = "${PLAYWRIGHT_MCP_VERSION}"
    CHROME_DEVTOOLS_MCP_VERSION = "${CHROME_DEVTOOLS_MCP_VERSION}"
    MCP_PROXY_VERSION           = "${MCP_PROXY_VERSION}"
    LIGHTHOUSE_VERSION          = "${LIGHTHOUSE_VERSION}"
  }
  tags = [
    "${REGISTRY}/${NAMESPACE}/pwmcp:${PWMCP_VERSION}",
    "${REGISTRY}/${NAMESPACE}/pwmcp:${PLAYWRIGHT_VERSION}",
    "${REGISTRY}/${NAMESPACE}/pwmcp:latest",
    "${REGISTRY}/${NAMESPACE}/pwmcp:latest-npm",
  ]
}

// The OCR sidecar ships with every pwmcp release under the SAME release coordinate, in its own
// GHCR repository (pwmcp-ocr), so a stack bundle's two image tags always match.
target "pwmcp-ocr" {
  context    = "."
  dockerfile = "containers/ocr/Dockerfile"
  args = {
    DEBIAN_RELEASE      = "${DEBIAN_RELEASE}"
    DEBIAN_IMAGE_DIGEST = "${DEBIAN_IMAGE_DIGEST}"
  }
  tags = [
    "${REGISTRY}/${NAMESPACE}/pwmcp-ocr:${PWMCP_VERSION}",
    "${REGISTRY}/${NAMESPACE}/pwmcp-ocr:latest",
  ]
}

group "all" {
  targets = ["pwmcp-latest", "pwmcp-ocr"]
}
