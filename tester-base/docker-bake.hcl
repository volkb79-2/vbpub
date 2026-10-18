// tester-base - the minimal gate base image. Two targets, one Dockerfile:
//   tester-base-local    the developer build (loaded as tester-base:local)
//   tester-base-release  the ONE release build: loaded under tester-base:local AND the registry
//                        names, so the contract lane tests the very image that scripts/release_image.py
//                        later pushes (docker push never rebuilds). Nothing pushes from bake.
variable "REGISTRY" {
  default = "ghcr.io"
}

variable "NAMESPACE" {
  default = "volkb79-2"
}

// The CMRU release coordinate; CMRU writes ./VERSION before the build step.
variable "TESTER_BASE_VERSION" {
  default = "0.1.0"
}

target "tester-base-local" {
  context    = "."
  dockerfile = "Dockerfile"
  tags       = ["tester-base:local"]
}

target "tester-base-release" {
  inherits = ["tester-base-local"]
  tags = [
    "tester-base:local",
    "${REGISTRY}/${NAMESPACE}/tester-base:${TESTER_BASE_VERSION}",
    "${REGISTRY}/${NAMESPACE}/tester-base:latest",
  ]
  labels = {
    "org.opencontainers.image.version" = "${TESTER_BASE_VERSION}"
  }
}
