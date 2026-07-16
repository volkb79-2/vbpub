//go:build systemdintegration

// Integration test for wings-slice-manager against a REAL systemd and a REAL
// Docker daemon. Intended to run inside the privileged systemd e2e harness
// (see ../test/e2e-systemd/); skips cleanly anywhere systemd or Docker is
// unavailable.
//
//	go test -tags systemdintegration -count=1 -v -run Integration ./...
package integration

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"os"
	"testing"
	"time"

	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/image"
	"github.com/docker/docker/client"

	"wings-slice-manager/internal/dockersrc"
	"wings-slice-manager/internal/manager"
	"wings-slice-manager/internal/spec"
	"wings-slice-manager/internal/sysd"
)

const (
	itestSlice     = "wings-itest.slice"
	itestContainer = "wings-slicemgr-itest"
)

func TestIntegrationReconcileCreatesSliceWithProperties(t *testing.T) {
	if _, err1 := os.Stat("/run/dbus/system_bus_socket"); err1 != nil {
		if _, err2 := os.Stat("/run/systemd/private"); err2 != nil {
			t.Skip("no systemd D-Bus endpoint available; skipping")
		}
	}

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()

	var buf bytes.Buffer
	log := slog.New(slog.NewTextHandler(io.MultiWriter(os.Stderr, &buf), &slog.HandlerOptions{Level: slog.LevelDebug}))

	cli, err := client.NewClientWithOpts(client.FromEnv, client.WithAPIVersionNegotiation())
	if err != nil {
		t.Skipf("docker client unavailable: %v", err)
	}
	defer cli.Close()
	if _, err := cli.Ping(ctx); err != nil {
		t.Skipf("docker daemon unreachable: %v", err)
	}

	sd, err := sysd.New(ctx, log)
	if err != nil {
		t.Skipf("systemd unavailable: %v", err)
	}
	defer sd.Close()

	// Pull busybox and create a container placed in the managed namespace
	// with a property spec, exactly as patched Wings (T2) would produce.
	rc, err := cli.ImagePull(ctx, "docker.io/library/busybox:latest", image.PullOptions{})
	if err != nil {
		t.Fatalf("pulling busybox: %v", err)
	}
	_, _ = io.Copy(io.Discard, rc)
	rc.Close()

	_ = cli.ContainerRemove(ctx, itestContainer, container.RemoveOptions{Force: true})
	// CgroupParent is a promoted field from the embedded container.Resources
	// struct, so it cannot be set in a composite literal.
	hostConf := &container.HostConfig{}
	hostConf.CgroupParent = itestSlice
	created, err := cli.ContainerCreate(ctx,
		&container.Config{
			Image: "busybox:latest",
			Cmd:   []string{"sleep", "120"},
			Env: []string{
				"WINGS_CGROUP_PARENT=" + itestSlice,
				"WINGS_CG_MEMORY_MIN=64M",
				"WINGS_CG_MEMORY_HIGH=128M",
				"WINGS_CG_CPU_WEIGHT=200",
			},
		},
		hostConf,
		nil, nil, itestContainer)
	if err != nil {
		t.Fatalf("creating container: %v", err)
	}
	t.Cleanup(func() {
		cctx, ccancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer ccancel()
		_ = cli.ContainerRemove(cctx, created.ID, container.RemoveOptions{Force: true})
		_ = sd.StopSlice(cctx, itestSlice)
	})
	if err := cli.ContainerStart(ctx, created.ID, container.StartOptions{}); err != nil {
		t.Fatalf("starting container: %v", err)
	}

	src, err := dockersrc.New(log)
	if err != nil {
		t.Fatalf("docker source: %v", err)
	}
	defer src.Close()

	m := manager.New(manager.Config{
		Namespace:         spec.Namespace{Prefix: "wings-", Parent: "wings.slice"},
		BudgetPolicy:      "clamp",
		ReconcileInterval: time.Minute,
		EventDebounce:     time.Second,
		GCGrace:           5 * time.Minute,
	}, src, sd, log)

	if err := m.ReconcileOnce(ctx); err != nil {
		t.Fatalf("reconcile: %v", err)
	}

	min, ok, err := sd.GetMemoryMin(ctx, itestSlice)
	if err != nil || !ok {
		t.Fatalf("reading MemoryMin of %s: ok=%v err=%v", itestSlice, ok, err)
	}
	if min != 64<<20 {
		t.Errorf("MemoryMin = %d, want %d (64M)", min, 64<<20)
	}
}
