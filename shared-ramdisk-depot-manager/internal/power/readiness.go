package power

import (
	"context"
	"fmt"
	"regexp"
	"strings"
	"time"
)

// ReadinessKindLogMatch is the one member of today's readiness vocabulary:
// a server is ready when a configured pattern appears in its own log. Kind
// is an explicit discriminator rather than "a pattern was set" standing in
// for one, so a future structured signal is additive rather than a
// breaking change to what Readiness means.
const ReadinessKindLogMatch = "log-match"

// Readiness describes how to tell a server is actually ready, beyond "the
// power state is running" — Soulmask's main only starts accepting slave
// connections once its own world load finishes, and a slave that connects
// before that joins a session nobody finished initializing.
//
// Pattern follows the format this project's own eggs already use for
// log-derived events (the v1 cgroup patch stack's WINGS_CG_PHASE_EVENTS): a
// plain string is a substring match, and a "regex:" prefix makes it a
// regular expression.
type Readiness struct {
	Kind    string
	Pattern string
	Timeout time.Duration
}

// Configured reports whether a readiness check is asked for at all. An
// empty Readiness is a real, chosen state — decisions.md records it — not
// an oversight: a profile with no ready line to give trusts the power
// state alone.
func (r Readiness) Configured() bool { return r.Kind != "" }

// DefaultReadinessTimeout is how long WaitReady waits when Timeout is zero.
const DefaultReadinessTimeout = 5 * time.Minute

func (r Readiness) effectiveTimeout() time.Duration {
	if r.Timeout > 0 {
		return r.Timeout
	}
	return DefaultReadinessTimeout
}

// ReadinessAnchor is whatever a checker needs to tell evidence produced
// AFTER it apart from evidence that predates it. Opaque to callers: only a
// ReadinessChecker's own Anchor and WaitReady give it meaning.
type ReadinessAnchor []string

// ReadinessChecker waits for a server to become ready by some signal
// external to its own power state.
//
// Split into two calls rather than one, and that split is load-bearing: a
// server's log is polled, not streamed with a server-side "since" filter
// the way a real-time log source might offer, so the only way to ignore a
// stale match is to have already SEEN it before the server started. Anchor
// must be called immediately BEFORE the caller issues the server's start;
// WaitReady only ever counts evidence Anchor had not already seen.
type ReadinessChecker interface {
	// Anchor captures a snapshot of serverID's current log tail. Call it
	// right before starting the server.
	Anchor(ctx context.Context, serverID string) (ReadinessAnchor, error)
	// WaitReady blocks until serverID signals ready by r — matching only
	// lines NOT present in anchor — r's timeout elapses (a
	// *ReadinessTimeoutError), or ctx is cancelled.
	WaitReady(ctx context.Context, serverID string, r Readiness, anchor ReadinessAnchor) error
}

// ReadinessTimeoutError is a WaitReady that never saw its signal before its
// timeout — it FAILS the update; there is no fallthrough to "assume ready".
type ReadinessTimeoutError struct {
	ServerID string
	Pattern  string
	Timeout  string
}

func (e *ReadinessTimeoutError) Error() string {
	return fmt.Sprintf("power: %s did not signal ready (pattern %q matched no new log line) "+
		"within %s", e.ServerID, e.Pattern, e.Timeout)
}

// LogReader is the log-tail surface WingsLogReadiness needs. *WingsDriver
// satisfies it; a narrow interface rather than the whole driver so tests
// can fake exactly this and nothing else.
type LogReader interface {
	Logs(ctx context.Context, serverID string, size int) ([]string, error)
}

// DefaultReadinessPollInterval is how often WaitReady re-reads the log tail.
const DefaultReadinessPollInterval = 2 * time.Second

// WingsLogReadiness implements ReadinessChecker by polling a server's own
// log tail through Wings' node API — GET /api/servers/<uuid>/logs?size=N —
// rather than the Docker socket: Wings' API already has to be reachable for
// power control, and this needs no second connection.
//
// The tail is POLLED, not streamed, and carries no per-line timestamp
// Wings exposes here — see decisions.md D-033 for the residual risk that
// follows from that and how Anchor/WaitReady's split bounds it.
type WingsLogReadiness struct {
	reader       LogReader
	tailSize     int
	pollInterval time.Duration
}

// NewWingsLogReadiness returns a checker reading up to DefaultLogTailSize
// lines at DefaultReadinessPollInterval.
func NewWingsLogReadiness(reader LogReader) *WingsLogReadiness {
	return &WingsLogReadiness{reader: reader, tailSize: DefaultLogTailSize, pollInterval: DefaultReadinessPollInterval}
}

// Anchor snapshots serverID's current log tail. The caller must call this
// BEFORE issuing the server's start — anything already in the snapshot
// never counts as evidence the NEW run produced.
func (w *WingsLogReadiness) Anchor(ctx context.Context, serverID string) (ReadinessAnchor, error) {
	lines, err := w.reader.Logs(ctx, serverID, w.tailSize)
	if err != nil {
		return nil, fmt.Errorf("power: reading %s's log to anchor readiness: %w", serverID, err)
	}
	return ReadinessAnchor(lines), nil
}

// WaitReady polls serverID's log tail until a line NOT present in anchor
// matches r's pattern, r's timeout elapses, or ctx is cancelled.
func (w *WingsLogReadiness) WaitReady(ctx context.Context, serverID string, r Readiness,
	anchor ReadinessAnchor) error {

	if !r.Configured() {
		return nil
	}
	if r.Kind != ReadinessKindLogMatch {
		return fmt.Errorf("power: unknown readiness kind %q (want %q)", r.Kind, ReadinessKindLogMatch)
	}
	matches, err := compileMatcher(r.Pattern)
	if err != nil {
		return err
	}
	timeout := r.effectiveTimeout()
	deadline := time.Now().Add(timeout)

	seen := make(map[string]bool, len(anchor))
	for _, l := range anchor {
		seen[l] = true
	}

	for {
		lines, err := w.reader.Logs(ctx, serverID, w.tailSize)
		if err == nil {
			for _, l := range lines {
				if seen[l] {
					continue
				}
				if matches(l) {
					return nil
				}
			}
			// Folded into `seen` so a line already checked and found not
			// matching is not re-evaluated every poll — correctness does
			// not depend on this, it only avoids repeat work.
			for _, l := range lines {
				seen[l] = true
			}
		}
		if time.Now().After(deadline) {
			return &ReadinessTimeoutError{ServerID: serverID, Pattern: r.Pattern, Timeout: timeout.String()}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(w.pollInterval):
		}
	}
}

// compileMatcher applies the project's own log-derived-event convention: a
// "regex:" prefix is a regular expression, anything else is a plain
// substring match. See profile.Readiness's doc comment for where the
// convention comes from.
func compileMatcher(pattern string) (func(string) bool, error) {
	if expr, ok := strings.CutPrefix(pattern, "regex:"); ok {
		re, err := regexp.Compile(expr)
		if err != nil {
			return nil, fmt.Errorf("power: readiness pattern %q does not compile: %w", pattern, err)
		}
		return re.MatchString, nil
	}
	return func(s string) bool { return strings.Contains(s, pattern) }, nil
}
