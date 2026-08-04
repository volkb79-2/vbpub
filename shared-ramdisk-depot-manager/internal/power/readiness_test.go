package power

import (
	"context"
	"errors"
	"testing"
	"time"
)

// fakeLogReader stands in for WingsDriver's log tail: each call to Logs
// pops the next scripted response, so a test can script exactly what a
// server "printed" across successive polls.
type fakeLogReader struct {
	responses [][]string // one per call; the last one repeats once exhausted
	calls     int
	err       error
}

func (f *fakeLogReader) Logs(_ context.Context, _ string, _ int) ([]string, error) {
	if f.err != nil {
		return nil, f.err
	}
	i := f.calls
	if i >= len(f.responses) {
		i = len(f.responses) - 1
	}
	f.calls++
	return f.responses[i], nil
}

func TestWaitReadyMatchesANewLineNotInTheAnchor(t *testing.T) {
	reader := &fakeLogReader{responses: [][]string{
		{"starting up", "World load complete."},
		{"starting up", "World load complete.", "registe server soulmask session succeed"},
	}}
	w := &WingsLogReadiness{reader: reader, tailSize: 200, pollInterval: time.Millisecond}

	anchor, err := w.Anchor(ctx(), "server-a")
	if err != nil {
		t.Fatal(err)
	}
	err = w.WaitReady(ctx(), "server-a",
		Readiness{Kind: ReadinessKindLogMatch, Pattern: "registe server soulmask session succeed", Timeout: time.Second},
		anchor)
	if err != nil {
		t.Fatalf("WaitReady: %v", err)
	}
}

// The defining property (oracle 33): a matching line already present at
// Anchor time — from the PREVIOUS run, still sitting in the tail — must
// never count, however many times it keeps appearing across polls.
func TestWaitReadyIgnoresALineAlreadyInTheAnchor(t *testing.T) {
	stale := []string{"registe server soulmask session succeed"} // from the old run
	reader := &fakeLogReader{responses: [][]string{stale, stale, stale}}
	w := &WingsLogReadiness{reader: reader, tailSize: 200, pollInterval: time.Millisecond}

	anchor, err := w.Anchor(ctx(), "server-a") // captures the stale line
	if err != nil {
		t.Fatal(err)
	}
	err = w.WaitReady(ctx(), "server-a",
		Readiness{Kind: ReadinessKindLogMatch, Pattern: "registe server soulmask session succeed", Timeout: 30 * time.Millisecond},
		anchor)
	var to *ReadinessTimeoutError
	if !errors.As(err, &to) {
		t.Fatalf("want a *ReadinessTimeoutError (the stale line must not count), got %v", err)
	}
}

func TestWaitReadyTimesOutWhenNothingEverMatches(t *testing.T) {
	reader := &fakeLogReader{responses: [][]string{{"starting"}, {"still starting"}}}
	w := &WingsLogReadiness{reader: reader, tailSize: 200, pollInterval: time.Millisecond}

	err := w.WaitReady(ctx(), "server-a",
		Readiness{Kind: ReadinessKindLogMatch, Pattern: "ready", Timeout: 20 * time.Millisecond}, nil)
	var to *ReadinessTimeoutError
	if !errors.As(err, &to) {
		t.Fatalf("want a *ReadinessTimeoutError, got %v (%T)", err, err)
	}
	if to.ServerID != "server-a" {
		t.Errorf("ReadinessTimeoutError does not name the server: %+v", to)
	}
}

func TestWaitReadyUnconfiguredIsANoOp(t *testing.T) {
	reader := &fakeLogReader{err: errors.New("must not be called")}
	w := &WingsLogReadiness{reader: reader, tailSize: 200, pollInterval: time.Millisecond}
	if err := w.WaitReady(ctx(), "server-a", Readiness{}, nil); err != nil {
		t.Fatalf("an unconfigured Readiness made a request and failed: %v", err)
	}
}

func TestAnchorPropagatesAReadError(t *testing.T) {
	reader := &fakeLogReader{err: errors.New("wings unreachable")}
	w := NewWingsLogReadiness(reader)
	if _, err := w.Anchor(ctx(), "server-a"); err == nil {
		t.Fatal("Anchor succeeded despite the log read failing")
	}
}

func TestWaitReadyRejectsAnUnknownKind(t *testing.T) {
	w := NewWingsLogReadiness(&fakeLogReader{err: errors.New("must not be called")})
	if err := w.WaitReady(ctx(), "server-a", Readiness{Kind: "rcon-probe"}, nil); err == nil {
		t.Fatal("an unknown readiness kind was accepted")
	}
}

func TestWaitReadyRejectsAnUncompilableRegexPattern(t *testing.T) {
	w := NewWingsLogReadiness(&fakeLogReader{err: errors.New("must not be called")})
	err := w.WaitReady(ctx(), "server-a",
		Readiness{Kind: ReadinessKindLogMatch, Pattern: "regex:(unclosed"}, nil)
	if err == nil {
		t.Fatal("an uncompilable pattern was accepted")
	}
}

func TestCompileMatcherPlainIsASubstringMatch(t *testing.T) {
	m, err := compileMatcher("session succeed")
	if err != nil {
		t.Fatal(err)
	}
	if !m("registe server soulmask session succeed") {
		t.Error("plain pattern did not match a containing line")
	}
	if m("world load begin") {
		t.Error("plain pattern matched an unrelated line")
	}
}

func TestCompileMatcherRegexPrefixIsARegularExpression(t *testing.T) {
	m, err := compileMatcher(`regex:Success! App '\d+' \(already up to date\)`)
	if err != nil {
		t.Fatal(err)
	}
	if !m("Success! App '3017300' (already up to date)") {
		t.Error("regex pattern did not match")
	}
	if m("Success! App 'abc' (already up to date)") {
		t.Error("regex pattern matched a non-numeric app id")
	}
}

func TestNewWingsLogReadinessUsesDefaults(t *testing.T) {
	w := NewWingsLogReadiness(&fakeLogReader{})
	if w.tailSize != DefaultLogTailSize {
		t.Errorf("tailSize = %d, want %d", w.tailSize, DefaultLogTailSize)
	}
	if w.pollInterval != DefaultReadinessPollInterval {
		t.Errorf("pollInterval = %v, want %v", w.pollInterval, DefaultReadinessPollInterval)
	}
}
