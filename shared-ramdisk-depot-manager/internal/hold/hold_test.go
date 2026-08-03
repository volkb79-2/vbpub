package hold

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"srdm/internal/systemdx"
)

// recorder captures every command the systemd driver issues and replays
// scripted output, so argv construction and failure handling are asserted
// with no systemd present.
type recorder struct {
	calls   [][]string
	outputs []string
	errs    []error
	n       int
}

func (r *recorder) run(_ context.Context, name string, args ...string) (string, error) {
	r.calls = append(r.calls, append([]string{name}, args...))
	i := r.n
	r.n++
	var out string
	if i < len(r.outputs) {
		out = r.outputs[i]
	}
	var err error
	if i < len(r.errs) {
		err = r.errs[i]
	}
	return out, err
}

func newManager(t *testing.T, r *recorder, opts ...Option) *Manager {
	t.Helper()
	// The wait budget is shortened because the scripted answers here are
	// FIXED: a unit that has not reached `running` by the time the recorder
	// runs out of output never will, so this cannot flip a verdict — it only
	// decides how long a test spends confirming one. The sleep is a no-op,
	// so without it the poll spins on wall-clock for the full 60s.
	client := systemdx.New(
		systemdx.WithRunner(r.run),
		systemdx.WithSleep(func(time.Duration) {}),
		systemdx.WithWaitBudget(time.Second))
	m, err := New(append([]Option{WithClient(client), WithExe("/usr/bin/srdm")}, opts...)...)
	if err != nil {
		t.Fatal(err)
	}
	return m
}

func (r *recorder) call(t *testing.T, i int) []string {
	t.Helper()
	if i >= len(r.calls) {
		t.Fatalf("expected at least %d commands, got %d: %v", i+1, len(r.calls), r.calls)
	}
	return r.calls[i]
}

// --- naming: where the master plan's shape does not survive systemd -------

// ancestorSlices returns the slice units systemd nests `name` beneath,
// applying its own rule: a slice's parent is its name with one final
// "-"-separated component removed.
//
// Encoded here rather than assumed, and pinned by the test below against the
// ControlGroup systemd actually reported — an oracle built on a naming rule
// nobody checked would be exactly as wrong as the design it is testing.
func ancestorSlices(name string) []string {
	stem := strings.TrimSuffix(name, ".slice")
	var out []string
	for {
		i := strings.LastIndex(stem, "-")
		if i < 0 {
			break
		}
		stem = stem[:i]
		out = append(out, stem+".slice")
	}
	return out
}

// The rule above, against what systemd measurably did on 2026-08-03:
//
//	ControlGroup=/srdm.slice/srdm-gen.slice/srdm-gen-b5b5b5b5.slice/srdm-hold-b5b5b5b5-pak.service
func TestAncestorSlicesMatchesWhatSystemdDid(t *testing.T) {
	got := ancestorSlices("srdm-gen-b5b5b5b5.slice")
	want := []string{"srdm-gen.slice", "srdm.slice"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ancestorSlices = %v, want %v — systemd placed that unit under exactly "+
			"those, so this helper is wrong and every test using it is meaningless", got, want)
	}
	if len(ancestorSlices("srdm.slice")) != 0 {
		t.Error("a single-token slice was given ancestors")
	}
}

// The generation aggregate must nest DIRECTLY under the configured parent.
//
// The master plan's `srdm-gen-<g8>.slice` interposes an auto-created
// `srdm-gen.slice` that nobody owns and that carries memory.min=0. A cgroup
// v2 floor is capped by every ancestor's, so a zero anywhere on the chain
// makes every floor beneath it arithmetically dead — the exact failure the
// single-token root name exists to prevent, one level down. Measured; D-015.
func TestGenerationSliceInterposesNothing(t *testing.T) {
	name, err := SliceName("srdm.slice", "a1b2c3d4")
	if err != nil {
		t.Fatal(err)
	}
	if name != "srdm-a1b2c3d4.slice" {
		t.Fatalf("SliceName = %q", name)
	}
	if got := ancestorSlices(name); !reflect.DeepEqual(got, []string{"srdm.slice"}) {
		t.Fatalf("%s nests under %v; anything between it and the configured parent is a "+
			"slice srdm does not own, carrying memory.min=0, and every class floor below "+
			"it is arithmetically dead", name, got)
	}
}

// The parent's single-token shape is what makes the one-level derivation
// safe, so a hyphenated one is refused here as well as in config.Validate:
// otherwise the ancestors come back by another route.
func TestSliceNameRefusesAHyphenatedParent(t *testing.T) {
	_, err := SliceName("srdm-releases.slice", "a1b2c3d4")
	if err == nil {
		t.Fatal("a hyphenated parent slice was accepted")
	}
	if !strings.Contains(err.Error(), "arithmetically dead") {
		t.Errorf("the refusal does not explain the consequence: %v", err)
	}
	for _, bad := range []string{"", "srdm", "srdm.service"} {
		if _, err := SliceName(bad, "a1b2c3d4"); err == nil {
			t.Errorf("parent %q was accepted as a slice unit", bad)
		}
	}
}

func TestUnitNameCarriesTheGenerationAndClass(t *testing.T) {
	name, err := UnitName("a1b2c3d4", "pak")
	if err != nil {
		t.Fatal(err)
	}
	if name != "srdm-hold-a1b2c3d4-pak.service" {
		t.Fatalf("UnitName = %q", name)
	}
	// Everything here ends up in an argv and in a cgroup path, so a class
	// name that is not a legal unit name is refused rather than escaped.
	for _, bad := range []string{"pak/../..", "pak pak", "", "pak\n"} {
		if _, err := UnitName("a1b2c3d4", bad); err == nil {
			t.Errorf("class name %q was accepted into a unit name", bad)
		}
	}
}

// --- properties -----------------------------------------------------------

func TestPropertiesCarryTheShapeAndThenThePolicy(t *testing.T) {
	zero := int64(0)
	yes := true
	got := Properties("srdm-a1b2c3d4.slice", Policy{
		MemoryMin: 150 << 20, ZSwapMax: &zero, ZSwapWriteback: &yes})

	want := []systemdx.Property{
		// The shape first, and it is not negotiable: Type=notify so that
		// "active" means "populated" rather than "exec'd" (D-013), and
		// NotifyAccess=main so a helper that exits cannot declare a class
		// populated on the worker's behalf.
		{Name: "Type", Value: "notify"},
		{Name: "NotifyAccess", Value: "main"},
		// systemd's 90s default is not a failsafe for a worker that copies
		// and hashes gigabytes; it is a limit a legitimate population can hit.
		{Name: "TimeoutStartSec", Value: "1800s"},
		{Name: "Slice", Value: "srdm-a1b2c3d4.slice"},
		{Name: "MemoryMin", Value: "157286400"},
		{Name: "MemoryZSwapMax", Value: "0"},
		{Name: "MemoryZSwapWriteback", Value: "yes"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Properties =\n%v\nwant\n%v", got, want)
	}
}

// A knob the profile did not set must not appear at all. Writing zero for an
// unset MemoryZSwapMax would be indistinguishable from a class that
// deliberately bypasses zswap — and would silently do it to one that did not.
func TestPropertiesOmitUnsetKnobs(t *testing.T) {
	got := Properties("srdm-a1b2c3d4.slice", Policy{MemoryMin: 200 << 20})
	for _, p := range got {
		if strings.HasPrefix(p.Name, "MemoryZSwap") {
			t.Errorf("an unset zswap knob was rendered anyway: %v", p)
		}
	}
	none := Properties("", Policy{})
	for _, p := range none {
		if p.Name == "MemoryMin" || p.Name == "Slice" {
			t.Errorf("a class with no policy still rendered %v", p)
		}
	}
	// But the shape survives an empty policy, because the shape is not
	// policy: a unit without it holds nothing.
	want := []string{"Type", "NotifyAccess", "TimeoutStartSec"}
	if len(none) != len(want) {
		t.Fatalf("an empty policy dropped the hold shape: %v", none)
	}
	for i, name := range want {
		if none[i].Name != name {
			t.Fatalf("the hold shape is %v, want %v", none, want)
		}
	}
}

// --- starting a hold ------------------------------------------------------

func TestStartBuildsTheUnitAndWaitsForReady(t *testing.T) {
	r := &recorder{outputs: []string{
		"",                                     // systemd-run
		"SubState=running\nActiveState=active", // the readiness poll
	}}
	m := newManager(t, r)

	err := m.Start(context.Background(), Spec{
		Unit:        "srdm-hold-a1b2c3d4-pak.service",
		Slice:       "srdm-a1b2c3d4.slice",
		Description: "srdm hold: release rel-1 class pak",
		Policy:      Policy{MemoryMin: 150 << 20},
		Worker: WorkerArgs{
			ReleaseDir: "/var/lib/srdm/store/releases/rel-1",
			ReleaseID:  "rel-1",
			Class:      "pak",
			Target:     "/run/srdm/.op/op-1/pak/root",
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	want := []string{
		"systemd-run",
		"--unit=srdm-hold-a1b2c3d4-pak.service",
		"--description=srdm hold: release rel-1 class pak",
		"--property=Type=notify",
		"--property=NotifyAccess=main",
		"--property=TimeoutStartSec=1800s",
		"--property=Slice=srdm-a1b2c3d4.slice",
		"--property=MemoryMin=157286400",
		"--",
		// The worker IS srdm: one binary, invoked on itself, so the pages are
		// faulted inside the unit that carries the class policy.
		"/usr/bin/srdm", "hold-worker",
		"--release-dir", "/var/lib/srdm/store/releases/rel-1",
		"--release-id", "rel-1",
		"--class", "pak",
		"--target", "/run/srdm/.op/op-1/pak/root",
	}
	if got := r.call(t, 0); !reflect.DeepEqual(got, want) {
		t.Fatalf("systemd-run argv =\n%v\nwant\n%v", got, want)
	}

	// It waits for `running`, not `exited`. A healthy hold unit never exits;
	// anything waiting for `exited` is waiting for a failure (D-011).
	show := strings.Join(r.call(t, 1), " ")
	if !strings.Contains(show, "SubState") {
		t.Errorf("Start did not wait for the unit's state: %v", r.calls[1])
	}
}

func TestStartPassesWorkerEnvironment(t *testing.T) {
	r := &recorder{outputs: []string{"", "SubState=running\nActiveState=active"}}
	m := newManager(t, r, WithWorkerEnv("SRDM_E2E_WORKER=1"))

	if err := m.Start(context.Background(), Spec{
		Unit:   "srdm-hold-a1b2c3d4-pak.service",
		Worker: WorkerArgs{ReleaseDir: "/rel", ReleaseID: "r", Class: "pak", Target: "/t"},
	}); err != nil {
		t.Fatal(err)
	}
	if !containsArg(r.call(t, 0), "--property=Environment=SRDM_E2E_WORKER=1") {
		t.Fatalf("the worker environment did not reach the unit: %v", r.calls[0])
	}
}

// The worker's exit status crosses the process boundary through systemd, and
// it is what tells a refusal from a fault.
//
// It has to be read on the systemd-run error path and not only after the
// wait: systemd-run BLOCKS on the start job, and for a Type=notify unit that
// job does not finish until the worker signals ready — so a worker that fails
// reports through the start call itself. Measured, by an ENOSPC oracle that
// got a bare "exit status 1" when this path did not consult it.
func TestStartReportsTheWorkersExitStatus(t *testing.T) {
	r := &recorder{
		errs:    []error{errors.New("exit status 1: Job for ... failed")}, // systemd-run
		outputs: []string{"", "ActiveState=failed\nResult=exit-code", "ExecMainStatus=3"},
	}
	m := newManager(t, r)

	err := m.Start(context.Background(), startSpec())
	if err == nil {
		t.Fatal("Start reported success for a unit that failed")
	}
	if got := ExitStatus(err); got != ExitNoSpace {
		t.Fatalf("ExitStatus = %d, want %d — without it the daemon cannot tell a class "+
			"that does not fit from a broken one", got, ExitNoSpace)
	}
	if !strings.Contains(err.Error(), "out of space") {
		t.Errorf("the error does not say what the status meant: %v", err)
	}
}

// The same status, reached the other way: a systemd where the start call
// returns before the unit settles, so the wait is what notices.
func TestStartReportsTheExitStatusFoundByTheWait(t *testing.T) {
	r := &recorder{outputs: []string{
		"",                                     // systemd-run returned early
		"SubState=failed\nActiveState=failed",  // the poll sees it fail
		"ActiveState=failed\nResult=exit-code", // it failed by exiting
		"ExecMainStatus=3",
	}}
	m := newManager(t, r)

	err := m.Start(context.Background(), startSpec())
	if err == nil {
		t.Fatal("Start reported success for a unit that failed")
	}
	if got := ExitStatus(err); got != ExitNoSpace {
		t.Fatalf("ExitStatus = %d, want %d", got, ExitNoSpace)
	}
}

// A start refused before the worker ever ran must NOT report the status of
// whatever holds that unit name. For a healthy running unit ExecMainStatus is
// 0, so believing it would turn a failure into a success — the one mistake
// here that would be silent.
func TestStartIgnoresTheStatusOfAUnitThatDidNotFail(t *testing.T) {
	r := &recorder{
		errs: []error{errors.New("Unit srdm-hold-a1b2c3d4-pak.service already exists")},
		outputs: []string{
			"",
			"ActiveState=active\nResult=success",
			// Scripted deliberately, and it is the whole test: a running
			// unit's ExecMainStatus is 0. If the guard above is dropped, this
			// is what gets believed, and the failure is reported as a
			// worker that succeeded. Without this line the case would pass
			// either way — the mutation would simply run out of scripted
			// output — which is a hollow test, and the canary said so.
			"ExecMainStatus=0",
		},
	}
	m := newManager(t, r)

	err := m.Start(context.Background(), startSpec())
	if err == nil {
		t.Fatal("Start reported success after systemd refused it")
	}
	if got := ExitStatus(err); got != -1 {
		t.Fatalf("ExitStatus = %d, want -1 — the unit did not fail by exiting, so it has "+
			"no status to report; believing a running unit's ExecMainStatus reports a "+
			"failed start as a worker that succeeded", got)
	}
}

// A unit that never ran at all is genuinely different from one that ran and
// failed, and must not be reported as exit status 0.
func TestStartDistinguishesAUnitThatNeverRan(t *testing.T) {
	r := &recorder{
		errs:    []error{errors.New("exit status 1")},
		outputs: []string{"", "ActiveState=failed\nResult=exit-code", "ExecMainStatus=[not set]"},
	}
	m := newManager(t, r)

	err := m.Start(context.Background(), startSpec())
	if err == nil {
		t.Fatal("Start reported success for a unit that failed")
	}
	if got := ExitStatus(err); got != -1 {
		t.Fatalf("ExitStatus = %d for a unit with no main process on record, want -1", got)
	}
}

func startSpec() Spec {
	return Spec{
		Unit:   "srdm-hold-a1b2c3d4-pak.service",
		Worker: WorkerArgs{ReleaseDir: "/rel", ReleaseID: "r", Class: "pak", Target: "/t"},
	}
}

func TestStartRefusesAMalformedSpec(t *testing.T) {
	r := &recorder{}
	m := newManager(t, r)
	cases := map[string]Spec{
		"no unit name":  {Worker: WorkerArgs{ReleaseDir: "/r", ReleaseID: "i", Class: "c", Target: "/t"}},
		"not a service": {Unit: "srdm-hold.slice", Worker: WorkerArgs{ReleaseDir: "/r", ReleaseID: "i", Class: "c", Target: "/t"}},
		"no worker":     {Unit: "srdm-hold-a1-pak.service"},
		"relative path": {Unit: "srdm-hold-a1-pak.service", Worker: WorkerArgs{ReleaseDir: "rel", ReleaseID: "i", Class: "c", Target: "/t"}},
	}
	for name, spec := range cases {
		if err := m.Start(context.Background(), spec); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	if len(r.calls) != 0 {
		t.Errorf("a malformed spec still reached systemd: %v", r.calls)
	}
}

// --- the generation aggregate ---------------------------------------------

func TestEnsureSliceSetsTheFloorAsARuntimeDropIn(t *testing.T) {
	r := &recorder{outputs: []string{""}}
	m := newManager(t, r)

	if err := m.EnsureSlice(context.Background(), "srdm-a1b2c3d4.slice", 350<<20); err != nil {
		t.Fatal(err)
	}
	want := []string{"systemctl", "set-property", "--runtime",
		"srdm-a1b2c3d4.slice", "MemoryMin=367001600"}
	if got := r.call(t, 0); !reflect.DeepEqual(got, want) {
		t.Fatalf("set-property argv =\n%v\nwant\n%v", got, want)
	}
}

// --runtime, not a persistent drop-in. A generation's floor describes one
// live generation; surviving the reboot after which srdm republishes from its
// own records would leave a floor for content nobody has.
func TestEnsureSliceIsRuntimeOnly(t *testing.T) {
	r := &recorder{outputs: []string{""}}
	m := newManager(t, r)
	if err := m.EnsureSlice(context.Background(), "srdm-a1b2c3d4.slice", 1<<20); err != nil {
		t.Fatal(err)
	}
	if !containsArg(r.call(t, 0), "--runtime") {
		t.Fatalf("the floor was written persistently: %v", r.calls[0])
	}
}

// A generation whose classes declare no floors gets no drop-in at all.
// MemoryMin=0 is the default, so writing it would leave a drop-in that
// claims a policy while saying nothing.
func TestEnsureSliceWritesNothingWithoutAFloor(t *testing.T) {
	r := &recorder{}
	m := newManager(t, r)
	if err := m.EnsureSlice(context.Background(), "srdm-a1b2c3d4.slice", 0); err != nil {
		t.Fatal(err)
	}
	if len(r.calls) != 0 {
		t.Fatalf("a zero floor still produced a drop-in: %v", r.calls)
	}
}

func TestReleaseSliceStopsTheSliceAndDropsItsFloor(t *testing.T) {
	r := &recorder{outputs: []string{"", ""}}
	m := newManager(t, r)

	if err := m.ReleaseSlice(context.Background(), "srdm-a1b2c3d4.slice"); err != nil {
		t.Fatal(err)
	}
	// Stopping is not optional: measured, a slice stays active and keeps its
	// cgroup after its last service exits.
	if got := r.call(t, 0); !reflect.DeepEqual(got, []string{"systemctl", "stop", "srdm-a1b2c3d4.slice"}) {
		t.Fatalf("first call = %v, want the slice stop", got)
	}
	if got := r.call(t, 1); !reflect.DeepEqual(got, []string{"systemctl", "revert", "srdm-a1b2c3d4.slice"}) {
		t.Fatalf("second call = %v, want the drop-in revert", got)
	}
}

// Teardown is idempotent by design — it runs after crashes and against
// records whose units are already gone — so "there is no such unit" is the
// goal state, not an error.
func TestForgetAndReleaseToleratateAUnitSystemdDoesNotKnow(t *testing.T) {
	r := &recorder{errs: []error{
		errors.New("Failed to stop x.service: Unit x.service not loaded."),
		nil,
		errors.New("Failed to stop y.slice: Unit y.slice not loaded."),
		nil,
	}}
	m := newManager(t, r)

	if err := m.Forget(context.Background(), "srdm-hold-a1-pak.service"); err != nil {
		t.Errorf("Forget of an unknown unit failed: %v", err)
	}
	if err := m.ReleaseSlice(context.Background(), "srdm-a1.slice"); err != nil {
		t.Errorf("ReleaseSlice of an unknown slice failed: %v", err)
	}
}

// A stop that fails for any other reason is a real failure: teardown that
// swallowed it would report success while the cgroup, and the memory, stayed.
func TestForgetReportsARealStopFailure(t *testing.T) {
	r := &recorder{errs: []error{errors.New("Access denied"), nil}}
	m := newManager(t, r)
	if err := m.Forget(context.Background(), "srdm-hold-a1-pak.service"); err == nil {
		t.Fatal("a failed stop was reported as success")
	}
}

// reset-failed runs even when the stop worked. A transient unit left in a
// failed state makes the next publication of the same generation fail by
// name, which reads as a flaky daemon rather than as leftover state.
func TestForgetAlwaysClearsFailedState(t *testing.T) {
	r := &recorder{outputs: []string{"", ""}}
	m := newManager(t, r)
	if err := m.Forget(context.Background(), "srdm-hold-a1-pak.service"); err != nil {
		t.Fatal(err)
	}
	if got := r.call(t, 1); !reflect.DeepEqual(got,
		[]string{"systemctl", "reset-failed", "srdm-hold-a1-pak.service"}) {
		t.Fatalf("second call = %v, want reset-failed", got)
	}
}

// --- unit state ------------------------------------------------------------

func TestIsActiveOnlyAcceptsARunningUnit(t *testing.T) {
	cases := map[string]bool{
		"ActiveState=active\nSubState=running":    true,
		"ActiveState=active\nSubState=exited":     false, // D-011: exited means the cgroup was reaped
		"ActiveState=activating\nSubState=start":  false, // still populating
		"ActiveState=failed\nSubState=failed":     false,
		"ActiveState=inactive\nSubState=dead":     false,
		"ActiveState=deactivating\nSubState=stop": false,
	}
	for out, want := range cases {
		r := &recorder{outputs: []string{out}}
		m := newManager(t, r)
		got, err := m.IsActive(context.Background(), "srdm-hold-a1-pak.service")
		if err != nil {
			t.Fatalf("%q: %v", out, err)
		}
		if got != want {
			t.Errorf("IsActive(%q) = %v, want %v", strings.ReplaceAll(out, "\n", " "), got, want)
		}
	}
}

func containsArg(args []string, want string) bool {
	for _, a := range args {
		if a == want {
			return true
		}
	}
	return false
}
