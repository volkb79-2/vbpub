// tester-base - the minimal gate base image.
//
// ONE build target. The release builds it once as tester-base:local; the contract lane tests that
// image; scripts/release_image.py later gives that very image its registry names (`docker tag`,
// after cmru has written the final VERSION) and pushes them. Nothing in this file knows a version:
// the version comes from the VERSION file cmru writes, and the script FAILS when it is absent.
target "tester-base-local" {
  context    = "."
  dockerfile = "Dockerfile"
  tags       = ["tester-base:local"]
}
