// Package power stops and starts the servers `srdm update` cycles.
//
// srdm already holds the Docker socket internal/consumer's Docker resolver
// uses to inspect container mounts, and could stop a container through it
// directly. It must not. Wings owns the container lifecycle: a container
// that dies underneath it is a crash Wings will act on — marking the server
// crashed and possibly restarting it, racing the very swap this package
// exists to make safe. Going through Wings' own node API — the same one
// wingsctl.py talks to — is not ceremony, it is the difference between an
// orchestrated update and two systems fighting over one container. It also
// keeps working when the Pterodactyl Panel itself is unreachable, which the
// Panel's own client API cannot offer (D-032).
//
// One interface, one real implementation (WingsDriver), and a fake for
// tests — the same shape internal/expose's Driver already uses for the
// exposure fork.
package power

import (
	"context"
	"errors"
	"fmt"
)

// State is a server's power state, as Wings reports it.
type State string

const (
	// StateRunning is a server accepting players.
	StateRunning State = "running"
	// StateOffline is a server fully stopped — what Stop waits for.
	StateOffline State = "offline"
	// StateStarting is a server on its way up — what Start waits past.
	StateStarting State = "starting"
	// StateStopping is a server on its way down — what Stop waits past.
	StateStopping State = "stopping"
	// StateUnknown is a state WingsDriver did not recognize, or a Status
	// call that failed outright. Never a settle target: treating it as one
	// would mean an API outage looks identical to success.
	StateUnknown State = "unknown"
)

// Driver stops and starts one server.
//
// It is the power surface `opctl.Update` swaps a server through — the same
// class of dependency `internal/expose.Driver` is for exposure, and for the
// same reason: publication, exposure and consumer resolution stay testable
// with no privilege and no real Wings node because the thing that talks to
// either is behind one small interface.
type Driver interface {
	// Name identifies the driver in records and journal entries.
	Name() string
	// Status reports a server's current power state.
	Status(ctx context.Context, serverID string) (State, error)
	// Stop asks a server to stop and blocks until it has settled offline,
	// or returns an error naming what did not happen.
	Stop(ctx context.Context, serverID string) error
	// Start asks a server to start and blocks until it is running, or
	// returns an error naming what did not happen.
	Start(ctx context.Context, serverID string) error
}

// RefusalError is the driver declining by contract: a precondition srdm
// cannot repair for the operator. Same discipline as expose.RefusalError —
// a refusal always carries its fix.
type RefusalError struct {
	// Precondition names which one.
	Precondition string
	// Detail is what srdm actually found.
	Detail string
	// Fix is what to do about it. Never empty.
	Fix string
}

func (e *RefusalError) Error() string {
	return fmt.Sprintf("power: refused (%s): %s; fix: %s", e.Precondition, e.Detail, e.Fix)
}

// IsRefusal reports whether err is the driver declining by contract.
func IsRefusal(err error) bool {
	var r *RefusalError
	return errors.As(err, &r)
}

// PreconditionUnconfigured names the one precondition this package checks
// today: enough of Config was set to build a driver at all.
const PreconditionUnconfigured = "unconfigured"

// SettleError is a Stop or Start that did not reach the state it was
// waiting for before its timeout.
//
// Deliberately not a RefusalError: a refusal is srdm declining before
// trying, because it already knows the attempt cannot work. A settle
// timeout is the opposite — srdm asked, Wings accepted the signal, and the
// server simply has not gotten there yet. Retrying, or waiting longer,
// might still work, which is exactly the distinction IsRefusal exists to
// preserve (see decisions.md D-032 for what "settled" means and why the two
// timeouts differ).
type SettleError struct {
	ServerID string
	Want     State
	Got      State
	Timeout  string
}

func (e *SettleError) Error() string {
	return fmt.Sprintf("power: server %s did not settle to %s within %s (last seen: %s)",
		e.ServerID, e.Want, e.Timeout, e.Got)
}
