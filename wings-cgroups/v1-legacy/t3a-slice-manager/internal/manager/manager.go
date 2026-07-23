// Package manager holds the reconcile core: desired-state computation from
// container metadata (pure, unit-testable), budget enforcement, the reconcile
// loop, and orphan-slice garbage collection. Docker and systemd sit behind
// interfaces.
package manager

import (
	"context"
	"log/slog"
	"math"
	"sort"
	"time"

	"wings-slice-manager/internal/spec"
)

// ContainerInfo is the daemon's view of one Docker container.
type ContainerInfo struct {
	ID           string
	Name         string
	Env          []string
	CgroupParent string // HostConfig.CgroupParent — the authoritative placement
	Running      bool
}

// ContainerSource lists containers and signals change events.
type ContainerSource interface {
	List(ctx context.Context) ([]ContainerInfo, error)
	// Events returns a channel that receives a value whenever containers
	// changed (created/started/stopped/destroyed). The channel is closed when
	// the underlying stream ends; the manager then relies on the periodic
	// reconcile only.
	Events(ctx context.Context) (<-chan struct{}, error)
}

// SystemdClient is the reconciler's view of systemd. Implementations MUST be
// namespace-agnostic; the manager performs all namespace checks before
// calling.
type SystemdClient interface {
	// EnsureSlice creates the transient slice or updates its runtime
	// properties if it is already loaded.
	EnsureSlice(ctx context.Context, name string, props spec.Props) error
	// StopSlice stops (removes) a transient slice.
	StopSlice(ctx context.Context, name string) error
	// ListSlices returns loaded slice unit names matching prefix*.slice.
	ListSlices(ctx context.Context, prefix string) ([]string, error)
	// GetMemoryMin reads a unit's effective MemoryMin (ok=false if unknown).
	GetMemoryMin(ctx context.Context, unit string) (val uint64, ok bool, err error)
}

// Config is the resolved manager configuration.
type Config struct {
	Namespace         spec.Namespace
	MemoryMinBudget   uint64 // 0 = unlimited
	BudgetPolicy      string // "clamp" | "refuse"
	ReconcileInterval time.Duration
	EventDebounce     time.Duration
	GCGrace           time.Duration
	DryRun            bool
}

// DesiredSlice is one managed slice with its requested properties and the
// containers that produced them.
type DesiredSlice struct {
	Name       string
	Props      spec.Props
	Containers []string
}

// Manager drives reconciliation.
type Manager struct {
	cfg Config
	src ContainerSource
	sd  SystemdClient
	log *slog.Logger

	// now is injectable for GC tests.
	now func() time.Time
	// orphanSince tracks when a managed slice was first seen without any
	// container mapping to it.
	orphanSince map[string]time.Time
}

// New builds a Manager.
func New(cfg Config, src ContainerSource, sd SystemdClient, log *slog.Logger) *Manager {
	return &Manager{
		cfg:         cfg,
		src:         src,
		sd:          sd,
		log:         log,
		now:         time.Now,
		orphanSince: map[string]time.Time{},
	}
}

// ComputeDesired derives the desired slice set from running containers.
// Containers are processed sorted by name; when several containers map to the
// same slice with different property requests, the last one (by name order)
// wins and a warning is logged. The MemoryMin budget is applied over slices
// sorted by name so clamping is deterministic.
func ComputeDesired(containers []ContainerInfo, cfg Config, log *slog.Logger) map[string]*DesiredSlice {
	sorted := make([]ContainerInfo, 0, len(containers))
	for _, c := range containers {
		if c.Running {
			sorted = append(sorted, c)
		}
	}
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Name < sorted[j].Name })

	desired := map[string]*DesiredSlice{}
	for _, c := range sorted {
		if !cfg.Namespace.ValidSliceName(c.CgroupParent) {
			log.Debug("ignoring container outside managed namespace",
				"container", c.Name, "cgroup_parent", c.CgroupParent)
			continue
		}
		requested, props, errs := spec.FromEnv(c.Env)
		for _, err := range errs {
			log.Warn("invalid cgroup spec value on container; value skipped",
				"container", c.Name, "error", err)
		}
		if requested != "" && requested != c.CgroupParent {
			log.Warn("container placement differs from requested WINGS_CGROUP_PARENT (using actual placement)",
				"container", c.Name, "requested", requested, "actual", c.CgroupParent)
		}
		d, exists := desired[c.CgroupParent]
		if !exists {
			d = &DesiredSlice{Name: c.CgroupParent}
			desired[c.CgroupParent] = d
		}
		d.Containers = append(d.Containers, c.Name)
		if props.Empty() {
			continue
		}
		if !d.Props.Empty() && !d.Props.Equal(props) {
			log.Warn("conflicting cgroup specs for shared slice; last container (by name) wins",
				"slice", d.Name, "container", c.Name,
				"previous", d.Props.String(), "new", props.String())
		}
		d.Props = props
	}

	applyBudget(desired, cfg, log)
	return desired
}

// applyBudget enforces Σ MemoryMin <= MemoryMinBudget over slices in name
// order. Policy "clamp" reduces the offending floor to the remaining budget;
// "refuse" drops it entirely.
func applyBudget(desired map[string]*DesiredSlice, cfg Config, log *slog.Logger) {
	if cfg.MemoryMinBudget == 0 {
		return
	}
	names := sortedKeys(desired)
	var used uint64
	for _, name := range names {
		d := desired[name]
		if d.Props.MemoryMin == nil {
			continue
		}
		want := *d.Props.MemoryMin
		if used+want <= cfg.MemoryMinBudget {
			used += want
			continue
		}
		remaining := uint64(0)
		if cfg.MemoryMinBudget > used {
			remaining = cfg.MemoryMinBudget - used
		}
		switch cfg.BudgetPolicy {
		case "refuse":
			log.Warn("MemoryMin floor exceeds node budget; refusing floor for slice",
				"slice", name, "requested", want, "budget", cfg.MemoryMinBudget, "already_allocated", used)
			d.Props.MemoryMin = nil
		default: // clamp
			log.Warn("MemoryMin floor exceeds node budget; clamping",
				"slice", name, "requested", want, "clamped_to", remaining,
				"budget", cfg.MemoryMinBudget, "already_allocated", used)
			clamped := remaining
			d.Props.MemoryMin = &clamped
			used += clamped
		}
	}
}

// ReconcileOnce performs one full reconcile pass: ensure desired slices,
// check the parent floor invariant, and garbage-collect orphaned slices.
func (m *Manager) ReconcileOnce(ctx context.Context) error {
	containers, err := m.src.List(ctx)
	if err != nil {
		m.log.Error("listing containers failed", "error", err)
		return err
	}

	desired := ComputeDesired(containers, m.cfg, m.log)
	m.checkParentInvariant(ctx, desired)

	for _, name := range sortedKeys(desired) {
		d := desired[name]
		if m.cfg.DryRun {
			m.log.Info("dry-run: would ensure slice", "slice", name,
				"props", d.Props.String(), "containers", d.Containers)
			continue
		}
		if err := m.sd.EnsureSlice(ctx, name, d.Props); err != nil {
			m.log.Error("ensuring slice failed", "slice", name, "error", err)
			continue
		}
		m.log.Debug("ensured slice", "slice", name, "props", d.Props.String())
	}

	m.gc(ctx, containers, desired)
	return nil
}

// checkParentInvariant warns when the admin-owned parent slice's MemoryMin is
// smaller than the sum of the child floors it must cover (children then
// compete proportionally for the shortfall — the guarantees are soft).
func (m *Manager) checkParentInvariant(ctx context.Context, desired map[string]*DesiredSlice) {
	var sum uint64
	for _, d := range desired {
		if d.Props.MemoryMin != nil {
			sum += *d.Props.MemoryMin
		}
	}
	if sum == 0 {
		return
	}
	parentMin, ok, err := m.sd.GetMemoryMin(ctx, m.cfg.Namespace.Parent)
	if err != nil || !ok {
		m.log.Debug("could not read parent slice MemoryMin", "parent", m.cfg.Namespace.Parent, "error", err)
		return
	}
	// systemd reports "infinity" as MaxUint64; anything that large covers all.
	if parentMin < sum && parentMin < math.MaxUint64/2 {
		m.log.Warn("parent slice MemoryMin is below the sum of child floors; child guarantees are not fully backed",
			"parent", m.cfg.Namespace.Parent, "parent_memory_min", parentMin, "children_sum", sum)
	}
}

// gc stops managed slices that no container (running or stopped) maps to,
// after they have been orphaned for at least GCGrace. Keeping slices of
// stopped containers alive avoids churn across server restarts.
func (m *Manager) gc(ctx context.Context, containers []ContainerInfo, desired map[string]*DesiredSlice) {
	keep := map[string]bool{}
	for _, c := range containers {
		if m.cfg.Namespace.ValidSliceName(c.CgroupParent) {
			keep[c.CgroupParent] = true
		}
	}
	for name := range desired {
		keep[name] = true
	}

	loaded, err := m.sd.ListSlices(ctx, m.cfg.Namespace.Prefix)
	if err != nil {
		m.log.Warn("listing slices for GC failed", "error", err)
		return
	}
	loadedSet := map[string]bool{}
	now := m.now()
	for _, name := range loaded {
		// The namespace guard is re-checked here: ListSlices patterns are a
		// convenience, not a security boundary.
		if !m.cfg.Namespace.ValidSliceName(name) {
			continue
		}
		loadedSet[name] = true
		if keep[name] {
			delete(m.orphanSince, name)
			continue
		}
		first, seen := m.orphanSince[name]
		if !seen {
			m.orphanSince[name] = now
			m.log.Debug("slice orphaned; grace period started", "slice", name, "grace", m.cfg.GCGrace)
			continue
		}
		if now.Sub(first) < m.cfg.GCGrace {
			continue
		}
		if m.cfg.DryRun {
			m.log.Info("dry-run: would stop orphaned slice", "slice", name)
			continue
		}
		if err := m.sd.StopSlice(ctx, name); err != nil {
			m.log.Warn("stopping orphaned slice failed", "slice", name, "error", err)
			continue
		}
		m.log.Info("stopped orphaned slice", "slice", name, "orphaned_since", first)
		delete(m.orphanSince, name)
	}
	// Forget tracked orphans that are no longer loaded at all.
	for name := range m.orphanSince {
		if !loadedSet[name] {
			delete(m.orphanSince, name)
		}
	}
}

// Run reconciles at startup, then on every ReconcileInterval tick and
// (debounced) on container events, until ctx is cancelled.
func (m *Manager) Run(ctx context.Context) error {
	if err := m.ReconcileOnce(ctx); err != nil && ctx.Err() != nil {
		return ctx.Err()
	}

	events, err := m.src.Events(ctx)
	if err != nil {
		m.log.Warn("container event stream unavailable; relying on periodic reconcile only", "error", err)
		events = nil
	}

	ticker := time.NewTicker(m.cfg.ReconcileInterval)
	defer ticker.Stop()

	var pending <-chan time.Time
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			_ = m.ReconcileOnce(ctx)
		case _, ok := <-events:
			if !ok {
				m.log.Warn("container event stream closed; relying on periodic reconcile only")
				events = nil
				continue
			}
			pending = time.After(m.cfg.EventDebounce)
		case <-pending:
			pending = nil
			_ = m.ReconcileOnce(ctx)
		}
	}
}

func sortedKeys(m map[string]*DesiredSlice) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
