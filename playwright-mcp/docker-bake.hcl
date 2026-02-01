variable "REGISTRY" {
  default = "ghcr.io"
}

variable "NAMESPACE" {
  default = "volkb79-2"
}

variable "IMAGE_NAME" {
  default = "playwright-mcp"
}

variable "VERSION" {
  default = "latest"
}

variable "BUILD_DATE" {
  default = "19700101"
}

variable "OCI_TITLE" {
  default = "playwright-mcp"
}

variable "OCI_DESCRIPTION" {
  default = "Standalone Playwright MCP server with WebSocket + health endpoints, auth support, and workspace/screenshot volumes for browser automation." 
}

variable "OCI_SOURCE" {
  default = "https://github.com/volkb79-2/vbpub"
}

variable "OCI_DOCUMENTATION" {
  default = "https://github.com/volkb79-2/vbpub/tree/main/playwright-mcp"
}

variable "OCI_URL" {
  default = "https://github.com/volkb79-2/vbpub/tree/main/playwright-mcp"
}

variable "OCI_LICENSES" {
  default = "MIT"
}

variable "OCI_VENDOR" {
  default = "volkb79-2"
}

variable "OCI_VERSION" {
  default = "${VERSION}"
}

variable "OCI_REVISION" {
  default = "unknown"
}

variable "OCI_CREATED" {
  default = "unknown"
}

function "tag" {
  params = [version]
  result = "${REGISTRY}/${NAMESPACE}/${IMAGE_NAME}:${version}-${BUILD_DATE}"
}

function "latest_tag" {
  params = [version]
  result = "${REGISTRY}/${NAMESPACE}/${IMAGE_NAME}:${version}"
}

function "static_latest_tag" {
  params = []
  result = "${REGISTRY}/${NAMESPACE}/${IMAGE_NAME}:latest"
}

target "playwright-mcp" {
  context = "."
  dockerfile = "Dockerfile"
  args = {
    OCI_TITLE = "${OCI_TITLE}"
    OCI_DESCRIPTION = "${OCI_DESCRIPTION}"
    OCI_SOURCE = "${OCI_SOURCE}"
    OCI_DOCUMENTATION = "${OCI_DOCUMENTATION}"
    OCI_URL = "${OCI_URL}"
    OCI_LICENSES = "${OCI_LICENSES}"
    OCI_VENDOR = "${OCI_VENDOR}"
    OCI_VERSION = "${OCI_VERSION}"
    OCI_REVISION = "${OCI_REVISION}"
    OCI_CREATED = "${OCI_CREATED}"
  }
  tags = [tag("${VERSION}"), latest_tag("${VERSION}"), static_latest_tag()]
}

group "all" {
  targets = ["playwright-mcp"]
}
