variable "REGISTRY" {
  default = "ghcr.io"
}

variable "NAMESPACE" {
  default = "volkb79-2"
}

variable "PLAYWRIGHT_VERSION" {
  default = "1.61.0"
}

variable "PLAYWRIGHT_DISTRO" {
  default = "noble"
}

variable "PWMCP_VERSION" {
  default = "1.61.0-r1"
}

variable "OCI_SOURCE" {
  default = "https://github.com/volkb79-2/vbpub"
}

variable "OCI_DOCUMENTATION" {
  default = "https://github.com/volkb79-2/vbpub/tree/main/pwmcp"
}

target "pwmcp" {
  context    = "."
  dockerfile = "containers/pwmcp/Dockerfile"
  args = {
    PLAYWRIGHT_VERSION = "${PLAYWRIGHT_VERSION}"
    PLAYWRIGHT_DISTRO  = "${PLAYWRIGHT_DISTRO}"
  }
  tags = [
    "${REGISTRY}/${NAMESPACE}/pwmcp:${PWMCP_VERSION}",
    "${REGISTRY}/${NAMESPACE}/pwmcp:${PLAYWRIGHT_VERSION}",
    "${REGISTRY}/${NAMESPACE}/pwmcp:latest",
  ]
}

group "all" {
  targets = ["pwmcp"]
}
