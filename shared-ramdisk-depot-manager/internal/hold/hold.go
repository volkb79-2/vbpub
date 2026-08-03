// Package hold owns the systemd units that carry a published generation's
// memory.
//
// The design's central resource claim is that a generation's pages are
// charged to a cgroup which also carries that class's memory policy, and
// that the two can never separate. That is only true if the process which
// FAULTS the pages runs inside that cgroup. So population does not happen in
// the daemon and get accounted for afterwards: it happens in the hold unit,
// which then parks so the cgroup outlives it (D-011), and signals ready only
// once the content is actually there (D-013).
//
// Three things therefore have to agree, and this package owns all three: the
// unit and slice names, the properties that carry the policy, and the worker
// that runs inside.
package hold

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"

	"srdm/internal/systemdx"
)

// Worker exit codes. They are the daemon's refusal channel: a hold unit that
// fails leaves its status in systemd's own `ExecMainStatus`, which the daemon
// reads back to tell "this class cannot hold its content" (a refusal, and the
// generation is quarantined) from "something broke" (a fault). See D-017.
const (
	// ExitOK is never returned by a healthy worker, which parks instead of
	// exiting. It is what a worker returns when it is asked to stop.
	ExitOK = 0
	// ExitFault is any failure with no more specific meaning.
	ExitFault = 1
	// ExitUsage is a malformed invocation — a programming error in the
	// daemon, not a condition of the content.
	ExitUsage = 2
	// ExitNoSpace is ENOSPC while populating: the class tmpfs cannot hold
	// what the manifest says the class contains. This is the 2026-07-29
	// corruption shape, and it is a refusal.
	ExitNoSpace = 3
	// ExitVerify is content that does not match the manifest. The class
	// never becomes a bind.
	ExitVerify = 4
)

// UnitName returns the hold unit for one class of one generation.
//
// Hyphens are free here: only SLICE names nest in systemd, so a service may
// carry as many separators as it likes without acquiring ancestors.
func UnitName(gen, class string) (string, error) {
	if gen == "" || class == "" {
		return "", fmt.Errorf("hold: a unit name needs both a generation and a class (got %q, %q)", gen, class)
	}
	name := "srdm-hold-" + gen + "-" + class + ".service"
	if err := systemdx.ValidUnitName(name); err != nil {
		return "", err
	}
	return name, nil
}

// SliceName returns the per-generation aggregate slice, and it is
// deliberately NOT the master plan's `srdm-gen-<g8>.slice`.
//
// systemd reads "-" in a SLICE name as the hierarchy separator, so
// `srdm-gen-a1b2c3d4.slice` nests beneath an auto-created `srdm-gen.slice`
// that nobody owns and that carries `memory.min=0`. A cgroup v2 floor is
// capped by every ancestor's floor, so a zero anywhere on the chain makes
// every floor below it arithmetically dead. Measured on systemd 257,
// 2026-08-03:
//
//	/srdm.slice/srdm-gen.slice/srdm-gen-b5b5b5b5.slice/srdm-hold-...service
//	memory.min:   0     ->      0        ->   367001600    ->    157286400
//
// That is the exact failure the single-token root name (master-plan decision
// 9) exists to prevent, arriving one level down. `<parent>-<g8>.slice` nests
// DIRECTLY under the configured parent and interposes nothing:
//
//	/srdm.slice/srdm-a1b2c3d4.slice/srdm-hold-a1b2c3d4-pak.service
//	memory.min: 536870912 -> 367001600 -> 157286400
//
// See decision D-015. The parent's own single-token shape is what makes the
// derivation safe, which is why a hyphenated parent is refused here as well
// as in config.Validate.
func SliceName(parent, gen string) (string, error) {
	if gen == "" {
		return "", errors.New("hold: a generation slice needs a generation id")
	}
	stem, ok := strings.CutSuffix(parent, ".slice")
	if !ok || stem == "" {
		return "", fmt.Errorf("hold: parent %q is not a slice unit", parent)
	}
	if strings.Contains(stem, "-") {
		return "", fmt.Errorf("hold: parent slice %q contains %q, which systemd reads as a "+
			"hierarchy separator; a generation slice beneath it would inherit auto-created "+
			"ancestors carrying MemoryMin=0 and every class floor would be arithmetically dead",
			parent, "-")
	}
	name := stem + "-" + gen + ".slice"
	if err := systemdx.ValidUnitName(name); err != nil {
		return "", err
	}
	return name, nil
}

// Policy is one class's memory policy, as the profile declares it.
type Policy struct {
	// MemoryMin is the class floor in bytes. Zero means no floor is
	// declared, and the property is then not set at all rather than set to
	// zero — so a reader can tell "no policy" from "a policy of none".
	MemoryMin int64
	// ZSwapMax caps what the class may put in zswap. A pointer because zero
	// is a meaningful value — pak content is zstd-incompressible (measured
	// 1.006x), so compressing it burns CPU to not shrink — and is not the
	// same as leaving zswap at its default.
	ZSwapMax *int64
	// ZSwapWriteback allows the class's zswap pages to reach disk. Also a
	// pointer: unset is systemd's default, not "no".
	ZSwapWriteback *bool
}

// PopulateTimeout bounds how long a worker may take to report itself
// populated.
//
// It is a HANG failsafe, never an oracle: expiry means a worker that is not
// making progress, which is a true failure however long you wait. It is set
// explicitly because systemd's 90-second default is not a failsafe here — it
// is a limit a legitimate population can reach, since a class is gigabytes
// and the worker copies and hashes every byte of it before signalling. Thirty
// minutes is three orders of magnitude above the largest plausible class on
// the slowest plausible disk.
const PopulateTimeout = "1800s"

// Properties renders the policy together with the unit shape D-011 and D-013
// fixed. The shape comes first so a policy can never quietly replace it.
func Properties(slice string, p Policy) []systemdx.Property {
	props := systemdx.HoldBaseProperties()
	props = append(props, systemdx.Property{Name: "TimeoutStartSec", Value: PopulateTimeout})
	if slice != "" {
		props = append(props, systemdx.Property{Name: "Slice", Value: slice})
	}
	if p.MemoryMin > 0 {
		props = append(props, systemdx.Property{
			Name: "MemoryMin", Value: strconv.FormatInt(p.MemoryMin, 10)})
	}
	if p.ZSwapMax != nil {
		props = append(props, systemdx.Property{
			Name: "MemoryZSwapMax", Value: strconv.FormatInt(*p.ZSwapMax, 10)})
	}
	if p.ZSwapWriteback != nil {
		props = append(props, systemdx.Property{
			Name: "MemoryZSwapWriteback", Value: yesNo(*p.ZSwapWriteback)})
	}
	return props
}

func yesNo(b bool) string {
	if b {
		return "yes"
	}
	return "no"
}

// Spec is one class's hold unit, complete.
type Spec struct {
	Unit  string
	Slice string
	// Description carries the FULL release identity. The unit name carries
	// only the 8-hex generation, for readability; anyone debugging needs the
	// whole thing, and this is where systemd shows it.
	Description string
	Policy      Policy
	Worker      WorkerArgs
}

func (s Spec) validate() error {
	if err := requireService(s.Unit); err != nil {
		return err
	}
	if s.Slice != "" {
		if err := requireSlice(s.Slice); err != nil {
			return err
		}
	}
	return s.Worker.validate()
}

// requireService and requireSlice narrow systemdx's generic boundary check,
// which accepts either kind because it is about characters and length.
//
// Here the kind is load-bearing in both directions: a slice has no ExecStart,
// so a "hold unit" that is one holds nothing and never becomes ready; and a
// service cannot be an aggregate, so a floor set on one protects nothing
// below it. Both mistakes are silent — the second especially, since a
// property set on the wrong unit is accepted and simply does not apply.
func requireService(name string) error {
	if err := systemdx.ValidUnitName(name); err != nil {
		return err
	}
	if !strings.HasSuffix(name, ".service") {
		return fmt.Errorf("hold: %q is not a service unit, so it has no worker to run and "+
			"could never report itself populated", name)
	}
	return nil
}

func requireSlice(name string) error {
	if err := systemdx.ValidUnitName(name); err != nil {
		return err
	}
	if !strings.HasSuffix(name, ".slice") {
		return fmt.Errorf("hold: %q is not a slice unit, so it cannot be the aggregate a "+
			"generation's floors live under", name)
	}
	return nil
}

// Manager starts and stops hold units.
type Manager struct {
	sys *systemdx.Client
	exe string
	env []string
}

// Option configures a Manager.
type Option func(*Manager)

// WithClient injects the systemd driver.
func WithClient(c *systemdx.Client) Option { return func(m *Manager) { m.sys = c } }

// WithExe overrides the worker executable.
//
// The default is this process's own binary, because the worker IS srdm — one
// binary, invoked as `srdm hold-worker`. An override exists for the e2e
// harness, where the running process is a test binary.
func WithExe(path string) Option { return func(m *Manager) { m.exe = path } }

// WithWorkerEnv adds environment to every worker this Manager starts.
//
// Production sets none. It exists for the e2e harness, which makes its own
// test binary act as the worker rather than shipping a second executable into
// the container — the alternative being to build and install srdm inside the
// gate, which would test a different binary from the one under test.
func WithWorkerEnv(env ...string) Option {
	return func(m *Manager) { m.env = append(m.env, env...) }
}

// New returns a Manager driving the real systemd.
func New(opts ...Option) (*Manager, error) {
	m := &Manager{sys: systemdx.New()}
	for _, o := range opts {
		o(m)
	}
	if m.exe == "" {
		exe, err := os.Executable()
		if err != nil {
			return nil, fmt.Errorf("hold: cannot resolve this binary's own path, so no hold "+
				"unit can be given an ExecStart: %w", err)
		}
		m.exe = exe
	}
	return m, nil
}

// EnsureSlice gives the generation aggregate its floor BEFORE any hold unit
// starts in it.
//
// The order matters and is not cosmetic: a slice systemd creates implicitly
// when a service names it starts with `memory.min=0`, and every class floor
// beneath it is capped at zero for as long as that lasts — which would be
// exactly the window in which the pages are being faulted. Measured
// 2026-08-03: `systemctl set-property --runtime` succeeds on a slice with no
// unit file and no running instance, writes
// /run/systemd/system.control/<slice>.d/, and the value is in the kernel by
// the time the first service starts. See D-016.
//
// --runtime because the drop-in describes one live generation. It must not
// survive the reboot after which srdm republishes from its own records.
func (m *Manager) EnsureSlice(ctx context.Context, slice string, memoryMin int64) error {
	if err := requireSlice(slice); err != nil {
		return err
	}
	if memoryMin <= 0 {
		// Nothing to protect. Setting MemoryMin=0 explicitly would be
		// indistinguishable from the default and would leave a drop-in
		// claiming a policy that says nothing.
		return nil
	}
	return m.sys.SetProperty(ctx, slice, systemdx.Property{
		Name: "MemoryMin", Value: strconv.FormatInt(memoryMin, 10)})
}

// Start runs one class's hold unit and returns once it has signalled ready.
//
// "Ready" is the whole point of Type=notify here: it means the content is
// populated, verified and sealed, not merely that a process was exec'd
// (D-013). Anything the daemon does next — binding, exposing — is therefore
// acting on a finished tree rather than racing the worker.
func (m *Manager) Start(ctx context.Context, spec Spec) error {
	if err := spec.validate(); err != nil {
		return err
	}
	props := Properties(spec.Slice, spec.Policy)
	for _, e := range m.env {
		props = append(props, systemdx.Property{Name: "Environment", Value: e})
	}
	unit := systemdx.TransientUnit{
		Name:        spec.Unit,
		Description: spec.Description,
		Properties:  props,
		ExecStart:   append([]string{m.exe}, spec.Worker.Argv()...),
	}
	// systemd-run BLOCKS on the start job, and for a Type=notify unit that
	// job does not finish until the worker signals ready — so a worker that
	// fails reports through this call rather than through the wait below.
	// Measured 2026-08-03, by an ENOSPC oracle that got a bare "exit status
	// 1" because this path did not consult the worker's own status.
	if err := m.sys.StartTransient(ctx, unit); err != nil {
		return m.workerError(ctx, spec.Unit, err)
	}
	// A healthy hold unit's SubState is `running`, not `exited` — anything
	// waiting for `exited` is waiting for a failure (D-011).
	if _, err := m.sys.WaitForSubState(ctx, spec.Unit, "running"); err != nil {
		return m.workerError(ctx, spec.Unit, err)
	}
	return nil
}

// WorkerError is a hold unit that did not reach ready, carrying the worker's
// own exit status so the daemon can tell a refusal from a fault.
type WorkerError struct {
	Unit string
	// Status is the worker's exit code, or -1 when systemd reported none —
	// which happens when the unit never ran at all rather than ran and
	// failed, and is a genuinely different situation.
	Status int
	Err    error
}

func (e *WorkerError) Error() string {
	if e.Status < 0 {
		return fmt.Sprintf("hold: unit %s did not become ready and reported no exit status: %v",
			e.Unit, e.Err)
	}
	return fmt.Sprintf("hold: unit %s did not become ready; the worker exited %d (%s): %v",
		e.Unit, e.Status, describeExit(e.Status), e.Err)
}

func (e *WorkerError) Unwrap() error { return e.Err }

func describeExit(status int) string {
	switch status {
	case ExitNoSpace:
		return "out of space"
	case ExitVerify:
		return "content does not match the manifest"
	case ExitUsage:
		return "malformed invocation"
	default:
		return "fault"
	}
}

// ExitStatus reports the worker exit code carried by err, or -1.
func ExitStatus(err error) int {
	var we *WorkerError
	if errors.As(err, &we) {
		return we.Status
	}
	return -1
}

func (m *Manager) workerError(ctx context.Context, unit string, cause error) error {
	we := &WorkerError{Unit: unit, Status: -1, Err: cause}
	// Only a unit that actually failed by exiting has a status worth
	// reporting. A start refused before the worker ever ran — a name already
	// taken, say — would otherwise hand back the ExecMainStatus of whatever
	// holds that name, which for a healthy running unit is 0: a failure
	// reported as a success, and the one mistake here that would be silent.
	props, err := m.sys.Show(ctx, unit, "ActiveState", "Result")
	if err != nil || props["ActiveState"] != "failed" || props["Result"] != "exit-code" {
		return we
	}
	status, err := m.sys.MainExitStatus(ctx, unit)
	if err == nil {
		we.Status = status
	}
	return we
}

// IsActive reports whether a unit is currently active.
//
// Reconciliation needs this because a record and a mount can each outlive the
// hold that pays for them: content still mounted with its hold unit gone is
// charged to a dying cgroup that carries no policy at all.
func (m *Manager) IsActive(ctx context.Context, unit string) (bool, error) {
	if err := requireService(unit); err != nil {
		return false, err
	}
	props, err := m.sys.Show(ctx, unit, "ActiveState", "SubState")
	if err != nil {
		return false, err
	}
	return props["ActiveState"] == "active" && props["SubState"] == "running", nil
}

// Forget stops a hold unit and clears any failed state.
//
// Both halves matter. Without the reset a transient unit that failed lingers
// in systemd's state and the next publication of the same generation is
// refused by name — which reads as a flaky daemon rather than as leftover
// state.
func (m *Manager) Forget(ctx context.Context, unit string) error {
	if err := requireService(unit); err != nil {
		return err
	}
	stopErr := m.sys.Stop(ctx, unit)
	// reset-failed regardless: the unit may have been in a failed state
	// precisely because the stop had nothing to stop.
	_ = m.sys.ResetFailed(ctx, unit)
	if stopErr != nil && !isNotLoaded(stopErr) {
		return stopErr
	}
	return nil
}

// ReleaseSlice stops the generation aggregate and removes its drop-in.
//
// Stopping is not optional bookkeeping: measured 2026-08-03, a slice stays
// `active` after its last service exits and its cgroup directory stays
// present, so a teardown that only stops the services leaves the generation's
// cgroup behind.
func (m *Manager) ReleaseSlice(ctx context.Context, slice string) error {
	if err := requireSlice(slice); err != nil {
		return err
	}
	stopErr := m.sys.Stop(ctx, slice)
	// The drop-in goes even if the stop failed: leaving a floor behind for a
	// generation that no longer exists is worse than either.
	_ = m.sys.Revert(ctx, slice)
	if stopErr != nil && !isNotLoaded(stopErr) {
		return stopErr
	}
	return nil
}

// isNotLoaded recognises systemd declining to act on a unit it does not know.
//
// Teardown is idempotent by design — it runs after crashes, and against
// records whose units are already gone — so "there is no such unit" is the
// goal state, not an error.
func isNotLoaded(err error) bool {
	s := err.Error()
	return strings.Contains(s, "not loaded") || strings.Contains(s, "not found")
}
