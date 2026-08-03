// Package systemdx starts and inspects systemd transient units.
//
// srdm's publication layer runs each class's populate-and-hold worker as a
// transient unit, because that is the only shape in which the memory charge
// and the memory policy cannot separate: the pages a worker faults are
// charged to the cgroup it ran in and stay charged, so the unit that
// faulted them has to be the unit that carries MemoryMin.
//
// The driver is the systemd CLI rather than the D-Bus API. That keeps srdm
// dependency-free, which keeps its gate hermetic, and `systemd-run` /
// `systemctl` are stable interfaces. The master plan's privilege table
// names D-Bus; swapping the runner out later changes this file and nothing
// above it. See decision D-009.
package systemdx

import (
	"context"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// Property is a systemd unit property, as `--property=Name=Value`.
type Property struct {
	Name  string
	Value string
}

// TransientUnit describes a unit to start.
type TransientUnit struct {
	// Name must end in .service. It carries the generation and class, so it
	// is derived from content rather than chosen freely — hence the
	// validation below.
	Name        string
	Description string
	Properties  []Property
	// ExecStart is the argv, passed after `--`, so nothing here is
	// interpreted by a shell.
	ExecStart []string
}

// HoldBaseProperties are the properties a populate-and-hold unit must
// carry, and they are NOT the ones the master plan sketched.
//
// The plan specified `Type=oneshot` with `RemainAfterExit=yes`, leaving an
// explicit open branch: "if any systemd version fails to keep an
// active-but-empty service's cgroup alive, the fallback is an explicit
// minimal hold process — the oracle decides". The oracle has decided.
//
// Measured on systemd 257 (Debian trixie), 2026-08-03: such a unit does
// stay ActiveState=active with SubState=exited, and its declared MemoryMin
// is accepted — but systemd REAPS ITS CGROUP as soon as the last process
// exits. ControlGroup reads empty, MemoryCurrent reads "[not set]", the
// cgroup directory is gone, and every page the worker faulted reparents to
// system.slice, where the class floor does not apply. The floor would be
// arithmetically dead — the same failure the single-token slice name exists
// to prevent, arriving by a different route.
//
// A worker that parks after populating keeps the cgroup, the charge and the
// policy together, which is the property the whole design rests on. The
// worker must therefore NOT exit after populating.
//
// Type=notify, not Type=exec, and the difference is a race rather than a
// preference. Type=exec marks a unit active as soon as the process has been
// EXEC'D — which is before it has populated anything. Anything that waits
// for "running" and then acts on the content is then racing the worker: the
// e2e teardown oracle did exactly that and failed intermittently with EBUSY,
// because the unmount landed while the worker still had the file open
// (which is D-012's mechanism, arriving from the other direction).
//
// With Type=notify the worker signals READY=1 once population and
// verification are complete, so "active" means "the content is there and I
// am now only holding it". That is the state srdm actually needs to observe.
//
// See decisions D-011 and D-013, and the e2e oracles that pin both halves.
func HoldBaseProperties() []Property {
	return []Property{
		{Name: "Type", Value: "notify"},
		// Only the main process may signal readiness; a helper that exits
		// must not be able to declare the class populated.
		{Name: "NotifyAccess", Value: "main"},
	}
}

// CommandRunner runs a command and returns its combined stdout. It is
// injected so argv construction and output parsing are unit-tested with no
// systemd present.
type CommandRunner func(ctx context.Context, name string, args ...string) (string, error)

// Client drives systemd.
type Client struct {
	run   CommandRunner
	sleep func(time.Duration)
	// waitBudget bounds WaitForSubState. It is a HANG failsafe, never the
	// thing that decides a verdict: expiry is reported as "the unit never
	// reached <state>", which is a true failure, and the budget is two
	// orders of magnitude above the transition it waits for.
	waitBudget   time.Duration
	pollInterval time.Duration
}

// Option configures a Client.
type Option func(*Client)

// WithRunner injects the command runner.
func WithRunner(r CommandRunner) Option { return func(c *Client) { c.run = r } }

// WithSleep injects the poll delay, so a test never actually waits.
func WithSleep(f func(time.Duration)) Option { return func(c *Client) { c.sleep = f } }

// WithWaitBudget overrides the hang failsafe.
func WithWaitBudget(d time.Duration) Option { return func(c *Client) { c.waitBudget = d } }

// New returns a Client driving the real systemd CLI.
func New(opts ...Option) *Client {
	c := &Client{
		run:          execRunner,
		sleep:        time.Sleep,
		waitBudget:   60 * time.Second,
		pollInterval: 50 * time.Millisecond,
	}
	for _, o := range opts {
		o(c)
	}
	return c
}

func execRunner(ctx context.Context, name string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return string(out), fmt.Errorf("%s %s: %w: %s",
			name, strings.Join(args, " "), err, strings.TrimSpace(string(out)))
	}
	return string(out), nil
}

// ValidUnitName reports whether s is safe to pass to systemd as a unit name.
//
// Unit names are built from generation and class identifiers, so this is a
// boundary check, not decoration: everything here ends up in an argv and in
// a cgroup path.
func ValidUnitName(s string) error {
	if s == "" {
		return fmt.Errorf("systemdx: unit name is empty")
	}
	if !strings.HasSuffix(s, ".service") && !strings.HasSuffix(s, ".slice") {
		return fmt.Errorf("systemdx: unit name %q must end in .service or .slice", s)
	}
	if len(s) > 200 {
		return fmt.Errorf("systemdx: unit name %q is longer than 200 bytes", s)
	}
	for _, r := range s {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9',
			r == '-', r == '_', r == '.', r == '@', r == '\\':
		default:
			return fmt.Errorf("systemdx: unit name %q contains %q", s, string(r))
		}
	}
	return nil
}

// TransientArgs builds the systemd-run argv. Exported so the exact command
// is assertable without running it.
func TransientArgs(u TransientUnit) ([]string, error) {
	if err := ValidUnitName(u.Name); err != nil {
		return nil, err
	}
	if len(u.ExecStart) == 0 {
		return nil, fmt.Errorf("systemdx: unit %q has no ExecStart", u.Name)
	}
	args := []string{"--unit=" + u.Name}
	if u.Description != "" {
		args = append(args, "--description="+u.Description)
	}
	for _, p := range u.Properties {
		if p.Name == "" {
			return nil, fmt.Errorf("systemdx: unit %q has a property with no name", u.Name)
		}
		args = append(args, "--property="+p.Name+"="+p.Value)
	}
	// Everything after `--` is argv, never a shell string.
	args = append(args, "--")
	return append(args, u.ExecStart...), nil
}

// StartTransient starts a transient unit.
func (c *Client) StartTransient(ctx context.Context, u TransientUnit) error {
	args, err := TransientArgs(u)
	if err != nil {
		return err
	}
	_, err = c.run(ctx, "systemd-run", args...)
	return err
}

// Show reads unit properties. Values may contain "=", so each line is split
// on the first one only.
func (c *Client) Show(ctx context.Context, unit string, props ...string) (map[string]string, error) {
	if err := ValidUnitName(unit); err != nil {
		return nil, err
	}
	args := []string{"show", unit, "--no-pager"}
	if len(props) > 0 {
		args = append(args, "--property="+strings.Join(props, ","))
	}
	out, err := c.run(ctx, "systemctl", args...)
	if err != nil {
		return nil, err
	}
	return parseShow(out), nil
}

func parseShow(out string) map[string]string {
	res := make(map[string]string)
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimRight(line, "\r")
		if line == "" {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		res[key] = value
	}
	return res
}

// Property reads a single unit property.
func (c *Client) Property(ctx context.Context, unit, name string) (string, error) {
	props, err := c.Show(ctx, unit, name)
	if err != nil {
		return "", err
	}
	v, ok := props[name]
	if !ok {
		return "", fmt.Errorf("systemdx: unit %q reported no %s", unit, name)
	}
	return v, nil
}

// MainExitStatus reads a unit's main process exit code.
//
// This is how a worker reports a refusal to the daemon that started it: the
// process is not the daemon's child, so there is no wait status to read, but
// systemd keeps ExecMainStatus and hands it back. Measured 2026-08-03: a
// Type=notify unit whose worker exits 3 before signalling ready reports
// Result=exit-code, ActiveState=failed, ExecMainStatus=3.
//
// "[not set]" is systemd's way of saying the unit has no main process on
// record — a unit that never ran at all rather than one that ran and failed —
// and those are genuinely different situations, so it is an error here rather
// than a zero.
func (c *Client) MainExitStatus(ctx context.Context, unit string) (int, error) {
	v, err := c.Property(ctx, unit, "ExecMainStatus")
	if err != nil {
		return 0, err
	}
	n, err := strconv.Atoi(strings.TrimSpace(v))
	if err != nil {
		return 0, fmt.Errorf("systemdx: unit %q reports ExecMainStatus=%q, which is not an "+
			"exit status", unit, v)
	}
	return n, nil
}

// SetProperty applies runtime properties to a unit.
//
// --runtime writes the drop-in under /run, so it describes one boot and
// nothing more. That is the correct lifetime for anything srdm sets: a
// generation slice's floor belongs to a live generation, and srdm republishes
// from its own records after a reboot rather than from leftover drop-ins.
//
// It works on a unit that does not exist yet, which is what makes it usable
// at all here — a slice created implicitly by the first service that names it
// would otherwise be live with memory.min=0 for exactly the window in which
// the pages are faulted. Measured 2026-08-03; see D-016.
func (c *Client) SetProperty(ctx context.Context, unit string, props ...Property) error {
	if err := ValidUnitName(unit); err != nil {
		return err
	}
	if len(props) == 0 {
		return fmt.Errorf("systemdx: SetProperty on %q with no properties", unit)
	}
	args := []string{"set-property", "--runtime", unit}
	for _, p := range props {
		if p.Name == "" {
			return fmt.Errorf("systemdx: unit %q has a property with no name", unit)
		}
		args = append(args, p.Name+"="+p.Value)
	}
	_, err := c.run(ctx, "systemctl", args...)
	return err
}

// Revert removes the drop-ins srdm wrote for a unit.
//
// Paired with SetProperty: it is how a generation's floor stops existing when
// the generation does. srdm only ever set-properties units it names itself,
// so reverting one cannot discard an admin's configuration.
func (c *Client) Revert(ctx context.Context, unit string) error {
	if err := ValidUnitName(unit); err != nil {
		return err
	}
	_, err := c.run(ctx, "systemctl", "revert", unit)
	return err
}

// Stop stops a unit.
func (c *Client) Stop(ctx context.Context, unit string) error {
	if err := ValidUnitName(unit); err != nil {
		return err
	}
	_, err := c.run(ctx, "systemctl", "stop", unit)
	return err
}

// ResetFailed clears a unit's failed state so its name is reusable.
//
// Without it a transient unit that failed lingers in systemd's state and
// the next run of the same name is refused — which reads as a flaky test
// rather than as leftover state.
func (c *Client) ResetFailed(ctx context.Context, unit string) error {
	if err := ValidUnitName(unit); err != nil {
		return err
	}
	_, err := c.run(ctx, "systemctl", "reset-failed", unit)
	return err
}

// WaitForSubState polls until the unit reports one of the wanted sub-states.
//
// The budget is a hang failsafe, not an oracle. Expiry returns "never
// reached", which is a true failure — a unit that does not transition is
// broken, however long you wait. Shrinking the budget cannot turn a passing
// case into a failing one at any plausible speed, because the transition
// being waited on is sub-second and the budget is 60s.
func (c *Client) WaitForSubState(ctx context.Context, unit string, want ...string) (string, error) {
	if len(want) == 0 {
		return "", fmt.Errorf("systemdx: WaitForSubState needs at least one wanted state")
	}
	deadline := time.Now().Add(c.waitBudget)
	var last string
	for {
		props, err := c.Show(ctx, unit, "SubState", "ActiveState")
		if err != nil {
			return "", err
		}
		last = props["SubState"]
		for _, w := range want {
			if last == w {
				return last, nil
			}
		}
		if props["ActiveState"] == "failed" {
			return last, fmt.Errorf("systemdx: unit %q failed (SubState=%s) while waiting for %s",
				unit, last, strings.Join(want, "/"))
		}
		if time.Now().After(deadline) {
			return last, fmt.Errorf("systemdx: unit %q never reached %s (SubState=%s after %s)",
				unit, strings.Join(want, "/"), last, c.waitBudget)
		}
		if err := ctx.Err(); err != nil {
			return last, err
		}
		c.sleep(c.pollInterval)
	}
}
