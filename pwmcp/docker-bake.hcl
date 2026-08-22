variable "REGISTRY" {
  default = "ghcr.io"
}

variable "NAMESPACE" {
  default = "volkb79-2"
}

// One version is released only when npm, PyPI, and MCR all provide it.
variable "PLAYWRIGHT_VERSION" {
  default = "1.62.0"
}

variable "PLAYWRIGHT_DISTRO" {
  default = "noble"
}

// @playwright/mcp pin (bundled MCP HTTP/SSE server).
variable "PLAYWRIGHT_MCP_VERSION" {
  default = "0.0.76"
}

// chrome-devtools-mcp pin (stdio-only CDP MCP server).
variable "CHROME_DEVTOOLS_MCP_VERSION" {
  default = "1.5.0"
}

// mcp-proxy pin (stdio→streamable-HTTP proxy for chrome-devtools-mcp).
variable "MCP_PROXY_VERSION" {
  default = "6.5.2"
}

// lighthouse pin (Node API for programmatic audits).
// Used by the vendored in-repo lighthouse-mcp server.
variable "LIGHTHOUSE_VERSION" {
  default = "13.4.0"
}

// CMRU's prepared, Playwright-driven pwmcp release coordinate.
variable "PWMCP_VERSION" {
  default = "1.62.0-r2"
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

group "all" {
  targets = ["pwmcp-latest"]
}
