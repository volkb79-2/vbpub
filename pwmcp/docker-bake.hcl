variable "REGISTRY" {
  default = "ghcr.io"
}

variable "NAMESPACE" {
  default = "volkb79-2"
}

// PyPI playwright version — what `pip install playwright` yields.
// :latest tracks this version (canonical consumer alias).
variable "PLAYWRIGHT_VERSION_PYPI" {
  default = "1.61.0"
}

// npm playwright version — what `npm install playwright` yields.
// :latest-npm tracks this version.
variable "PLAYWRIGHT_VERSION_NPM" {
  default = "1.61.1"
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

// pwmcp release tag for the PyPI-based build (e.g. 1.60.0-r1).
variable "PWMCP_VERSION_PYPI" {
  default = "1.61.0-r6"
}

// pwmcp release tag for the npm-based build (e.g. 1.61.0-r1).
variable "PWMCP_VERSION_NPM" {
  default = "1.61.1-r2"
}

variable "OCI_SOURCE" {
  default = "https://github.com/volkb79-2/vbpub"
}

variable "OCI_DOCUMENTATION" {
  default = "https://github.com/volkb79-2/vbpub/tree/main/pwmcp"
}

// pwmcp-pypi-latest: tracks the PyPI playwright version.
// :latest alias always points to this build so that consumers running
// `pip install playwright` (which resolves the PyPI version) get a matching server.
// Consumer contract: pip install playwright==<X>  +  image: pwmcp:<X>  (or pwmcp:latest)
target "pwmcp-pypi-latest" {
  context    = "."
  dockerfile = "containers/pwmcp/Dockerfile"
  args = {
    PLAYWRIGHT_VERSION          = "${PLAYWRIGHT_VERSION_PYPI}"
    PLAYWRIGHT_DISTRO           = "${PLAYWRIGHT_DISTRO}"
    PLAYWRIGHT_MCP_VERSION      = "${PLAYWRIGHT_MCP_VERSION}"
    CHROME_DEVTOOLS_MCP_VERSION = "${CHROME_DEVTOOLS_MCP_VERSION}"
    MCP_PROXY_VERSION           = "${MCP_PROXY_VERSION}"
    LIGHTHOUSE_VERSION          = "${LIGHTHOUSE_VERSION}"
  }
  tags = [
    "${REGISTRY}/${NAMESPACE}/pwmcp:${PWMCP_VERSION_PYPI}",
    "${REGISTRY}/${NAMESPACE}/pwmcp:${PLAYWRIGHT_VERSION_PYPI}",
    "${REGISTRY}/${NAMESPACE}/pwmcp:latest",
  ]
}

// pwmcp-npm-latest: tracks the npm playwright version.
// :latest-npm alias points to this build for consumers that pin the npm version explicitly.
target "pwmcp-npm-latest" {
  context    = "."
  dockerfile = "containers/pwmcp/Dockerfile"
  args = {
    PLAYWRIGHT_VERSION          = "${PLAYWRIGHT_VERSION_NPM}"
    PLAYWRIGHT_DISTRO           = "${PLAYWRIGHT_DISTRO}"
    PLAYWRIGHT_MCP_VERSION      = "${PLAYWRIGHT_MCP_VERSION}"
    CHROME_DEVTOOLS_MCP_VERSION = "${CHROME_DEVTOOLS_MCP_VERSION}"
    MCP_PROXY_VERSION           = "${MCP_PROXY_VERSION}"
    LIGHTHOUSE_VERSION          = "${LIGHTHOUSE_VERSION}"
  }
  tags = [
    "${REGISTRY}/${NAMESPACE}/pwmcp:${PWMCP_VERSION_NPM}",
    "${REGISTRY}/${NAMESPACE}/pwmcp:${PLAYWRIGHT_VERSION_NPM}",
    "${REGISTRY}/${NAMESPACE}/pwmcp:latest-npm",
  ]
}

group "all" {
  targets = ["pwmcp-pypi-latest", "pwmcp-npm-latest"]
}
