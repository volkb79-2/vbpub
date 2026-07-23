FROM golang:1.25 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags '-s -w' -o /wings-slice-manager ./cmd/wings-slice-manager

FROM gcr.io/distroless/static:latest
COPY --from=build /wings-slice-manager /wings-slice-manager
# Containerized runs need the host control sockets:
#   -v /var/run/docker.sock:/var/run/docker.sock
#   -v /run/dbus/system_bus_socket:/run/dbus/system_bus_socket
# (or /run/systemd/private for the direct-systemd fallback)
ENTRYPOINT ["/wings-slice-manager"]
