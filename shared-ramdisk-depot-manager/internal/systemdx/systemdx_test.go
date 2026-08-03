package systemdx

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"
)

// recorder captures every command a Client issues and replays scripted
// output, so argv construction and parsing are asserted with no systemd.
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

func newClient(r *recorder, opts ...Option) *Client {
	base := []Option{WithRunner(r.run), WithSleep(func(time.Duration) {})}
	return New(append(base, opts...)...)
}

// --- argv construction ----------------------------------------------------

func TestTransientArgsBuildsTheHoldUnitInvocation(t *testing.T) {
	args, err := TransientArgs(TransientUnit{
		Name:        "srdm-hold-a1b2c3d4-pak.service",
		Description: "srdm hold: generation a1b2c3d4 class pak",
		Properties: []Property{
			{Name: "Type", Value: "oneshot"},
			{Name: "RemainAfterExit", Value: "yes"},
			{Name: "MemoryMin", Value: "150M"},
			{Name: "MemoryZSwapMax", Value: "0"},
		},
		ExecStart: []string{"/usr/bin/dd", "if=/dev/zero", "of=/run/srdm/blob"},
	})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"--unit=srdm-hold-a1b2c3d4-pak.service",
		"--description=srdm hold: generation a1b2c3d4 class pak",
		"--property=Type=oneshot",
		"--property=RemainAfterExit=yes",
		"--property=MemoryMin=150M",
		"--property=MemoryZSwapMax=0",
		"--",
		"/usr/bin/dd", "if=/dev/zero", "of=/run/srdm/blob",
	}
	if !reflect.DeepEqual(args, want) {
		t.Fatalf("TransientArgs =\n%v\nwant\n%v", args, want)
	}
}

// Everything after `--` is argv. A path with a space must reach the program
// as one argument rather than being re-split by a shell that is not there.
func TestTransientArgsPassesExecStartAsArgv(t *testing.T) {
	args, err := TransientArgs(TransientUnit{
		Name:      "x.service",
		ExecStart: []string{"/bin/sh", "-c", "echo hello world; true"},
	})
	if err != nil {
		t.Fatal(err)
	}
	sep := -1
	for i, a := range args {
		if a == "--" {
			sep = i
			break
		}
	}
	if sep < 0 {
		t.Fatal("no -- separator in the argv")
	}
	got := args[sep+1:]
	want := []string{"/bin/sh", "-c", "echo hello world; true"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("ExecStart argv = %v, want %v", got, want)
	}
}

func TestTransientArgsRejectsMalformedUnits(t *testing.T) {
	cases := map[string]TransientUnit{
		"no name":        {ExecStart: []string{"/bin/true"}},
		"no suffix":      {Name: "srdm-hold", ExecStart: []string{"/bin/true"}},
		"no exec":        {Name: "x.service"},
		"unnamed prop":   {Name: "x.service", ExecStart: []string{"/bin/true"}, Properties: []Property{{Value: "y"}}},
		"shell metachar": {Name: "x;rm -rf /.service", ExecStart: []string{"/bin/true"}},
		"slash in name":  {Name: "a/b.service", ExecStart: []string{"/bin/true"}},
	}
	for name, u := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := TransientArgs(u); err == nil {
				t.Fatal("a malformed transient unit was accepted")
			}
		})
	}
}

// Unit names carry generation and class ids and end up in an argv and a
// cgroup path, so the boundary check is load-bearing.
func TestValidUnitName(t *testing.T) {
	good := []string{
		"srdm.slice", "srdm-gen-a1b2c3d4.slice",
		"srdm-hold-a1b2c3d4-pak.service", "a_b.service", "x@1.service",
	}
	for _, s := range good {
		if err := ValidUnitName(s); err != nil {
			t.Errorf("ValidUnitName(%q) = %v", s, err)
		}
	}
	bad := []string{
		"", "no-suffix", "a b.service", "a;b.service", "a/b.service",
		"a$b.service", "a`b`.service", "a\nb.service",
		strings.Repeat("x", 200) + ".service",
	}
	for _, s := range bad {
		if err := ValidUnitName(s); err == nil {
			t.Errorf("ValidUnitName(%q) accepted it", s)
		}
	}
}

// --- show parsing ---------------------------------------------------------

func TestShowParsesPropertiesAndKeepsEqualsInValues(t *testing.T) {
	r := &recorder{outputs: []string{
		"ActiveState=active\n" +
			"SubState=exited\n" +
			"ControlGroup=/system.slice/srdm-hold-a1b2-pak.service\n" +
			// A value that itself contains '=' — split on the FIRST one only.
			"Environment=SRDM_CLASS=pak\n",
	}}
	c := newClient(r)

	props, err := c.Show(context.Background(), "srdm-hold-a1b2-pak.service",
		"ActiveState", "SubState", "ControlGroup", "Environment")
	if err != nil {
		t.Fatal(err)
	}
	if props["SubState"] != "exited" {
		t.Errorf("SubState = %q", props["SubState"])
	}
	if props["ControlGroup"] != "/system.slice/srdm-hold-a1b2-pak.service" {
		t.Errorf("ControlGroup = %q", props["ControlGroup"])
	}
	if props["Environment"] != "SRDM_CLASS=pak" {
		t.Errorf("a value containing = was truncated: %q", props["Environment"])
	}

	call := r.calls[0]
	if call[0] != "systemctl" || call[1] != "show" {
		t.Fatalf("unexpected command: %v", call)
	}
	joined := strings.Join(call, " ")
	if !strings.Contains(joined, "--property=ActiveState,SubState,ControlGroup,Environment") {
		t.Errorf("properties were not requested in one call: %v", call)
	}
}

func TestPropertyReportsAMissingField(t *testing.T) {
	r := &recorder{outputs: []string{"ActiveState=active\n"}}
	c := newClient(r)
	if _, err := c.Property(context.Background(), "x.service", "ControlGroup"); err == nil {
		t.Fatal("a missing property returned a value anyway")
	}
}

// --- waiting --------------------------------------------------------------

func TestWaitForSubStateReturnsOnceTheStateArrives(t *testing.T) {
	r := &recorder{outputs: []string{
		"ActiveState=activating\nSubState=start\n",
		"ActiveState=activating\nSubState=start\n",
		"ActiveState=active\nSubState=exited\n",
	}}
	c := newClient(r)

	got, err := c.WaitForSubState(context.Background(), "x.service", "exited")
	if err != nil {
		t.Fatal(err)
	}
	if got != "exited" {
		t.Fatalf("WaitForSubState = %q", got)
	}
	if r.n != 3 {
		t.Errorf("polled %d times, want 3", r.n)
	}
}

// A unit that has failed will never reach the wanted state. Reporting that
// immediately turns a 60-second hang into an actionable error.
func TestWaitForSubStateFailsFastOnAFailedUnit(t *testing.T) {
	r := &recorder{outputs: []string{"ActiveState=failed\nSubState=failed\n"}}
	c := newClient(r)

	_, err := c.WaitForSubState(context.Background(), "x.service", "exited")
	if err == nil {
		t.Fatal("waiting on a failed unit did not report the failure")
	}
	if !strings.Contains(err.Error(), "failed") {
		t.Errorf("error does not say the unit failed: %v", err)
	}
	if r.n != 1 {
		t.Errorf("polled %d times after a failure, want 1", r.n)
	}
}

// The budget is a HANG failsafe. Its expiry must read as a true failure —
// a unit that does not transition is broken however long you wait — not as
// a knob to widen.
func TestWaitForSubStateBudgetExpiryIsATrueFailure(t *testing.T) {
	r := &recorder{outputs: []string{"ActiveState=activating\nSubState=start\n"}}
	// Replay the same output forever.
	c := newClient(r, WithWaitBudget(0))

	_, err := c.WaitForSubState(context.Background(), "x.service", "exited")
	if err == nil {
		t.Fatal("the budget expired without an error")
	}
	if !strings.Contains(err.Error(), "never reached") {
		t.Errorf("expiry does not read as a failure to transition: %v", err)
	}
}

func TestWaitForSubStateNeedsAWantedState(t *testing.T) {
	c := newClient(&recorder{})
	if _, err := c.WaitForSubState(context.Background(), "x.service"); err == nil {
		t.Fatal("waiting for nothing was accepted")
	}
}

func TestWaitForSubStateAcceptsAnyOfSeveral(t *testing.T) {
	r := &recorder{outputs: []string{"ActiveState=active\nSubState=running\n"}}
	c := newClient(r)
	got, err := c.WaitForSubState(context.Background(), "x.service", "exited", "running")
	if err != nil {
		t.Fatal(err)
	}
	if got != "running" {
		t.Fatalf("WaitForSubState = %q", got)
	}
}

// --- lifecycle ------------------------------------------------------------

func TestStopAndResetFailedIssueTheRightCommands(t *testing.T) {
	r := &recorder{}
	c := newClient(r)
	ctx := context.Background()

	if err := c.Stop(ctx, "x.service"); err != nil {
		t.Fatal(err)
	}
	if err := c.ResetFailed(ctx, "x.service"); err != nil {
		t.Fatal(err)
	}

	want := [][]string{
		{"systemctl", "stop", "x.service"},
		{"systemctl", "reset-failed", "x.service"},
	}
	if !reflect.DeepEqual(r.calls, want) {
		t.Fatalf("calls =\n%v\nwant\n%v", r.calls, want)
	}
}

func TestLifecycleVerbsValidateTheUnitName(t *testing.T) {
	c := newClient(&recorder{})
	ctx := context.Background()
	for name, fn := range map[string]func() error{
		"Stop":        func() error { return c.Stop(ctx, "a b") },
		"ResetFailed": func() error { return c.ResetFailed(ctx, "a;b") },
		"Show":        func() error { _, err := c.Show(ctx, "a/b"); return err },
	} {
		if err := fn(); err == nil {
			t.Errorf("%s accepted an unsafe unit name", name)
		}
	}
}

func TestStartTransientPropagatesRunnerErrors(t *testing.T) {
	r := &recorder{errs: []error{errors.New("Failed to start transient service unit")}}
	c := newClient(r)
	err := c.StartTransient(context.Background(), TransientUnit{
		Name: "x.service", ExecStart: []string{"/bin/true"},
	})
	if err == nil {
		t.Fatal("a failing systemd-run was reported as success")
	}
}

func TestStartTransientIssuesSystemdRun(t *testing.T) {
	r := &recorder{}
	c := newClient(r)
	err := c.StartTransient(context.Background(), TransientUnit{
		Name:       "srdm-hold-a1b2-pak.service",
		Properties: []Property{{Name: "Type", Value: "oneshot"}},
		ExecStart:  []string{"/bin/true"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(r.calls) != 1 || r.calls[0][0] != "systemd-run" {
		t.Fatalf("calls = %v", r.calls)
	}
}

// The hold-unit shape is a measured design decision (D-011, D-013), not a
// preference, so it is pinned here rather than only living in a comment.
func TestHoldBasePropertiesAreProductionShaped(t *testing.T) {
	got := map[string]string{}
	for _, p := range HoldBaseProperties() {
		got[p.Name] = p.Value
	}

	// NOT oneshot+RemainAfterExit: systemd reaps an active-but-empty unit's
	// cgroup, and the class floor would be arithmetically dead (D-011).
	if got["Type"] != "notify" {
		t.Errorf("Type = %q, want notify", got["Type"])
	}
	if _, ok := got["RemainAfterExit"]; ok {
		t.Error("RemainAfterExit is set; the worker parks instead, so the unit " +
			"stays active because a process is alive in it")
	}
	// notify rather than exec: exec marks the unit active once the process
	// is EXEC'D, which is before it has populated anything (D-013).
	if got["NotifyAccess"] != "main" {
		t.Errorf("NotifyAccess = %q, want main — a helper that exits must not be "+
			"able to declare the class populated", got["NotifyAccess"])
	}
}
