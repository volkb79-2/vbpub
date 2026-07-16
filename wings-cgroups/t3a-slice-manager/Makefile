BIN := wings-slice-manager
GO  ?= go

.PHONY: build static test vet integration install clean

build:
	$(GO) build -o bin/$(BIN) ./cmd/$(BIN)

static:
	CGO_ENABLED=0 $(GO) build -trimpath -ldflags '-s -w' -o bin/$(BIN) ./cmd/$(BIN)

test:
	$(GO) test ./...

vet:
	$(GO) vet ./...
	$(GO) vet -tags systemdintegration ./...

# Requires a real systemd + docker (run inside the e2e harness).
integration:
	$(GO) test -tags systemdintegration -count=1 -v -run Integration ./...

install: static
	install -m 0755 bin/$(BIN) /usr/local/bin/$(BIN)
	install -m 0644 systemd/$(BIN).service /etc/systemd/system/$(BIN).service
	@test -f /etc/$(BIN).yaml || install -m 0644 config.example.yaml /etc/$(BIN).yaml
	systemctl daemon-reload
	@echo "now: systemctl enable --now $(BIN)"

clean:
	rm -rf bin
