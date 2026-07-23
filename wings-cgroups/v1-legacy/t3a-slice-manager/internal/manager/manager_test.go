package manager

import (
	"bytes"
	"context"
	"log/slog"
	"strings"
	"sync"
	"testing"
	"time"

	"wings-slice-manager/internal/spec"
)

func u64p(v uint64) *uint64 { return &v }

func testLogger(buf *bytes.Buffer) *slog.Logger {
	return slog.New(slog.NewTextHandler(buf, &slog.HandlerOptions{Level: slog.LevelDebug}))
}

func testConfig() Config {
	return Config{
		Namespace:         spec.Namespace{Prefix: "wings-", Parent: "wings.slice"},
		BudgetPolicy:      "clamp",
		ReconcileInterval: time.Minute,
		EventDebounce:     time.Second,
		GCGrace:           5 * time.Minute,
	}
}

type fakeSysd struct {
	mu        sync.Mutex
	ensured   map[string]spec.Props
	stopped   []string
	slices    []string
	parentMin uint64
	parentOK  bool
}

func newFakeSysd() *fakeSysd {
	return &fakeSysd{ensured: map[string]spec.Props{}}
}

func (f *fakeSysd) EnsureSlice(_ context.Context, name string, props spec.Props) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.ensured[name] = props
	return nil
}

func (f *fakeSysd) StopSlice(_ context.Context, name string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.stopped = append(f.stopped, name)
	return nil
}

func (f *fakeSysd) ListSlices(_ context.Context, _ string) ([]string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.slices...), nil
}

func (f *fakeSysd) GetMemoryMin(_ context.Context, _ string) (uint64, bool, error) {
	return f.parentMin, f.parentOK, nil
}

type fakeSrc struct {
	containers []ContainerInfo
}

func (f *fakeSrc) List(_ context.Context) ([]ContainerInfo, error) {
	return append([]ContainerInfo(nil), f.containers...), nil
}

func (f *fakeSrc) Events(_ context.Context) (<-chan struct{}, error) {
	return make(chan struct{}), nil
}

func TestComputeDesiredNamespaceGuard(t *testing.T) {
	var buf bytes.Buffer
	cfg := testConfig()
	containers := []ContainerInfo{
		{Name: "a", CgroupParent: "system.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=1G"}},
		{Name: "b", CgroupParent: "wings.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=1G"}},
		{Name: "c", CgroupParent: "", Running: true},
		{Name: "d", CgroupParent: "wings-good.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=1G"}},
		{Name: "e", CgroupParent: "wings-stopped.slice", Running: false, Env: []string{"WINGS_CG_MEMORY_MIN=1G"}},
	}
	desired := ComputeDesired(containers, cfg, testLogger(&buf))
	if len(desired) != 1 {
		t.Fatalf("desired = %v, want exactly wings-good.slice", desired)
	}
	d, ok := desired["wings-good.slice"]
	if !ok {
		t.Fatal("wings-good.slice missing from desired set")
	}
	if d.Props.MemoryMin == nil || *d.Props.MemoryMin != 1<<30 {
		t.Errorf("props = %s, want MemoryMin=1G", d.Props)
	}
}

func TestComputeDesiredSharedSliceLastWriterWins(t *testing.T) {
	var buf bytes.Buffer
	cfg := testConfig()
	containers := []ContainerInfo{
		{Name: "z-second", CgroupParent: "wings-shared.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=2G"}},
		{Name: "a-first", CgroupParent: "wings-shared.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=1G"}},
	}
	desired := ComputeDesired(containers, cfg, testLogger(&buf))
	d := desired["wings-shared.slice"]
	if d == nil {
		t.Fatal("missing shared slice")
	}
	if d.Props.MemoryMin == nil || *d.Props.MemoryMin != 2<<30 {
		t.Errorf("MemoryMin = %v, want 2G (z-second, last by name, wins)", d.Props.MemoryMin)
	}
	if len(d.Containers) != 2 {
		t.Errorf("containers = %v, want both recorded", d.Containers)
	}
	if !strings.Contains(buf.String(), "conflicting cgroup specs") {
		t.Error("expected a conflict warning in the log")
	}
}

func TestBudgetClamp(t *testing.T) {
	var buf bytes.Buffer
	cfg := testConfig()
	cfg.MemoryMinBudget = 8 << 30
	containers := []ContainerInfo{
		{Name: "a", CgroupParent: "wings-a.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=6G"}},
		{Name: "b", CgroupParent: "wings-b.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=6G"}},
	}
	desired := ComputeDesired(containers, cfg, testLogger(&buf))
	if got := *desired["wings-a.slice"].Props.MemoryMin; got != 6<<30 {
		t.Errorf("first slice MemoryMin = %d, want full 6G", got)
	}
	if got := *desired["wings-b.slice"].Props.MemoryMin; got != 2<<30 {
		t.Errorf("second slice MemoryMin = %d, want clamped 2G", got)
	}
	if !strings.Contains(buf.String(), "clamping") {
		t.Error("expected clamp warning in log")
	}
}

func TestBudgetRefuse(t *testing.T) {
	var buf bytes.Buffer
	cfg := testConfig()
	cfg.MemoryMinBudget = 8 << 30
	cfg.BudgetPolicy = "refuse"
	containers := []ContainerInfo{
		{Name: "a", CgroupParent: "wings-a.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=6G"}},
		{Name: "b", CgroupParent: "wings-b.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=6G"}},
	}
	desired := ComputeDesired(containers, cfg, testLogger(&buf))
	if desired["wings-a.slice"].Props.MemoryMin == nil {
		t.Error("first slice should keep its floor")
	}
	if desired["wings-b.slice"].Props.MemoryMin != nil {
		t.Error("second slice floor should be refused (nil)")
	}
}

func TestBudgetUnlimited(t *testing.T) {
	var buf bytes.Buffer
	cfg := testConfig() // MemoryMinBudget zero value = unlimited
	containers := []ContainerInfo{
		{Name: "a", CgroupParent: "wings-a.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=600G"}},
	}
	desired := ComputeDesired(containers, cfg, testLogger(&buf))
	if got := *desired["wings-a.slice"].Props.MemoryMin; got != 600<<30 {
		t.Errorf("MemoryMin = %d, want untouched 600G", got)
	}
}

func TestReconcileEnsuresAndGCRespectsGrace(t *testing.T) {
	var buf bytes.Buffer
	cfg := testConfig()
	sd := newFakeSysd()
	sd.slices = []string{"wings-live.slice", "wings-orphan.slice", "wings.slice"}
	src := &fakeSrc{containers: []ContainerInfo{
		{Name: "live", CgroupParent: "wings-live.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=1G"}},
	}}
	m := New(cfg, src, sd, testLogger(&buf))

	base := time.Now()
	m.now = func() time.Time { return base }
	if err := m.ReconcileOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, ok := sd.ensured["wings-live.slice"]; !ok {
		t.Error("live slice was not ensured")
	}
	if len(sd.stopped) != 0 {
		t.Errorf("stopped = %v; nothing may be GCed before the grace period", sd.stopped)
	}

	// Second pass still inside grace: nothing stopped.
	m.now = func() time.Time { return base.Add(time.Minute) }
	_ = m.ReconcileOnce(context.Background())
	if len(sd.stopped) != 0 {
		t.Errorf("stopped = %v; grace not yet elapsed", sd.stopped)
	}

	// Third pass beyond grace: orphan stopped, parent and live slice spared.
	m.now = func() time.Time { return base.Add(6 * time.Minute) }
	_ = m.ReconcileOnce(context.Background())
	if len(sd.stopped) != 1 || sd.stopped[0] != "wings-orphan.slice" {
		t.Errorf("stopped = %v, want exactly wings-orphan.slice", sd.stopped)
	}
}

func TestReconcileOrphanRecoversWhenContainerReturns(t *testing.T) {
	var buf bytes.Buffer
	cfg := testConfig()
	sd := newFakeSysd()
	sd.slices = []string{"wings-x.slice"}
	src := &fakeSrc{} // no containers: wings-x.slice is orphaned
	m := New(cfg, src, sd, testLogger(&buf))

	base := time.Now()
	m.now = func() time.Time { return base }
	_ = m.ReconcileOnce(context.Background())

	// The container comes back (stopped is enough to keep the slice).
	src.containers = []ContainerInfo{{Name: "x", CgroupParent: "wings-x.slice", Running: false}}
	m.now = func() time.Time { return base.Add(10 * time.Minute) }
	_ = m.ReconcileOnce(context.Background())
	if len(sd.stopped) != 0 {
		t.Errorf("stopped = %v; slice with a (stopped) container must be kept", sd.stopped)
	}
}

func TestReconcileDryRunTouchesNothing(t *testing.T) {
	var buf bytes.Buffer
	cfg := testConfig()
	cfg.DryRun = true
	cfg.GCGrace = 0
	sd := newFakeSysd()
	sd.slices = []string{"wings-orphan.slice"}
	src := &fakeSrc{containers: []ContainerInfo{
		{Name: "live", CgroupParent: "wings-live.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=1G"}},
	}}
	m := New(cfg, src, sd, testLogger(&buf))

	base := time.Now()
	m.now = func() time.Time { return base }
	_ = m.ReconcileOnce(context.Background())
	m.now = func() time.Time { return base.Add(time.Hour) }
	_ = m.ReconcileOnce(context.Background())

	if len(sd.ensured) != 0 || len(sd.stopped) != 0 {
		t.Errorf("dry-run performed actions: ensured=%v stopped=%v", sd.ensured, sd.stopped)
	}
	if !strings.Contains(buf.String(), "dry-run") {
		t.Error("expected dry-run log lines")
	}
}

func TestParentInvariantWarning(t *testing.T) {
	var buf bytes.Buffer
	cfg := testConfig()
	sd := newFakeSysd()
	sd.parentMin, sd.parentOK = 4<<30, true // parent floor 4G < child sum 6G
	src := &fakeSrc{containers: []ContainerInfo{
		{Name: "a", CgroupParent: "wings-a.slice", Running: true, Env: []string{"WINGS_CG_MEMORY_MIN=6G"}},
	}}
	m := New(cfg, src, sd, testLogger(&buf))
	_ = m.ReconcileOnce(context.Background())
	if !strings.Contains(buf.String(), "below the sum of child floors") {
		t.Error("expected parent invariant warning")
	}
}
