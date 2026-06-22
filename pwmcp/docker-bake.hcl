variable "REGISTRY" {
  default = "ghcr.io"
}

variable "NAMESPACE" {
  default = "volkb79-2"
}

// PyPI playwright version — what `pip install playwright` yields.
// :latest tracks this version (canonical consumer alias).
variable "PLAYWRIGHT_VERSION_PYPI" {
  default = "1.60.0"
}

// npm playwright version — what `npm install playwright` yields.
// :latest-npm tracks this version.
variable "PLAYWRIGHT_VERSION_NPM" {
  default = "1.61.0"
}

variable "PLAYWRIGHT_DISTRO" {
  default = "noble"
}

// pwmcp release tag for the PyPI-based build (e.g. 1.60.0-r1).
variable "PWMCP_VERSION_PYPI" {
  default = "1.60.0-r2"
}

// pwmcp release tag for the npm-based build (e.g. 1.61.0-r1).
variable "PWMCP_VERSION_NPM" {
  default = "1.61.0-r2"
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
    PLAYWRIGHT_VERSION = "${PLAYWRIGHT_VERSION_PYPI}"
    PLAYWRIGHT_DISTRO  = "${PLAYWRIGHT_DISTRO}"
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
    PLAYWRIGHT_VERSION = "${PLAYWRIGHT_VERSION_NPM}"
    PLAYWRIGHT_DISTRO  = "${PLAYWRIGHT_DISTRO}"
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
