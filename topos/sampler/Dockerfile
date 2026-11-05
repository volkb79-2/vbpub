# Build context is the topos/ directory (the tests read ../tests/fixtures):
#   docker build -f sampler/Dockerfile --target test  -t topos-sampler-test  .   # cargo test
#   docker build -f sampler/Dockerfile --target build -t topos-sampler-build .   # static musl binary
# The binary is /topos-sampler in the build image (docker create + docker cp), or:
#   docker build -f sampler/Dockerfile --target artifact -o type=local,dest=out .
#   docker buildx build -f sampler/Dockerfile --target gate --no-cache-filter test,conformance .
#     cargo test + conformance (the gate lane); the filter makes both test stages run again even when
#     their inputs are cached, while the toolchain, crates and Python dependency layers stay cached
FROM mirror.gcr.io/library/rust:1-slim AS base
RUN rustup target add x86_64-unknown-linux-musl
WORKDIR /src/sampler
COPY sampler/Cargo.toml sampler/Cargo.lock ./
RUN mkdir src && echo 'fn main(){}' > src/main.rs \
 && cargo build --locked --release --target x86_64-unknown-linux-musl; rm -rf src
COPY sampler/src ./src
COPY tests/fixtures /src/tests/fixtures

FROM base AS test
RUN cargo test --locked --target x86_64-unknown-linux-musl 2>&1 && touch /cargo-tests-passed

FROM base AS build
RUN touch src/main.rs && cargo build --locked --release --target x86_64-unknown-linux-musl \
 && cp target/x86_64-unknown-linux-musl/release/topos-sampler /topos-sampler

FROM scratch AS artifact
COPY --from=build /topos-sampler /topos-sampler

# The release binary against the Python collector (tests/test_sampler_conformance.py, fixture trees
# only). topos itself is not installed: its version comes from git (setuptools_scm). Its runtime
# dependencies, the zstandard and mcp extras the test conftest requires, and pytest are read from
# pyproject.toml, so the floors live in one place. pip takes the newest release above each floor.
FROM mirror.gcr.io/library/python:3.14-slim AS pydeps
WORKDIR /src/topos
COPY pyproject.toml ./
RUN python -c "import subprocess, sys, tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; o = p['optional-dependencies']; \
deps = p['dependencies'] + o['zstandard'] + o['mcp'] + [d for d in o['dev'] if d.startswith('pytest>')]; \
subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '--root-user-action=ignore', *deps])"

FROM pydeps AS conformance
COPY src ./src
COPY tests/fixtures ./tests/fixtures
COPY tests/conftest.py tests/test_sampler_conformance.py ./tests/
COPY sampler/Cargo.toml ./sampler/Cargo.toml
COPY --from=build /topos-sampler /usr/local/bin/topos-sampler
RUN PYTHONPATH=src:. TOPOS_REQUIRE_SAMPLER=1 TOPOS_SAMPLER_BIN=/usr/local/bin/topos-sampler \
    python -m pytest tests/test_sampler_conformance.py -q -rs -p no:cacheprovider

# Everything the gate lane needs in one build: cargo tests and conformance (the stages run in parallel)
FROM scratch AS gate
COPY --from=test /cargo-tests-passed /cargo-tests-passed
COPY --from=conformance /usr/local/bin/topos-sampler /topos-sampler
