# Build context is the topos/ directory (the tests read ../tests/fixtures):
#   docker build -f sampler/Dockerfile --target test  -t topos-sampler-test  .   # cargo test
#   docker build -f sampler/Dockerfile --target build -t topos-sampler-build .   # static musl binary
# The binary is /topos-sampler in the build image (docker create + docker cp), or:
#   docker build -f sampler/Dockerfile --target artifact -o type=local,dest=out .
FROM mirror.gcr.io/library/rust:1-slim AS base
RUN rustup target add x86_64-unknown-linux-musl
WORKDIR /src/sampler
COPY sampler/Cargo.toml sampler/Cargo.lock ./
RUN mkdir src && echo 'fn main(){}' > src/main.rs \
 && cargo build --locked --release --target x86_64-unknown-linux-musl; rm -rf src
COPY sampler/src ./src
COPY tests/fixtures /src/tests/fixtures

FROM base AS test
RUN cargo test --locked --target x86_64-unknown-linux-musl 2>&1

FROM base AS build
RUN touch src/main.rs && cargo build --locked --release --target x86_64-unknown-linux-musl \
 && cp target/x86_64-unknown-linux-musl/release/topos-sampler /topos-sampler

FROM scratch AS artifact
COPY --from=build /topos-sampler /topos-sampler
