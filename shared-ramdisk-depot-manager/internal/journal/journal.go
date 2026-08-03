// Package journal records what srdm did, durably.
//
// Three sinks, per the master plan: a durable per-operation record, an
// events JSONL stream, and structured journald events. One absolute rule
// (oracle O5): no credentials, ever. See redact.go — every record passes
// through the scrubber before it reaches any sink.
package journal

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"srdm/internal/fsx"
)

// SchemaVersion is the record version srdm writes. Versioned from day one.
const SchemaVersion = 1

// Outcome classifies where a phase ended up.
type Outcome string

const (
	// OutcomeStarted is written before a phase executes.
	OutcomeStarted Outcome = "started"
	// OutcomeOK is written after a phase succeeds.
	OutcomeOK Outcome = "ok"
	// OutcomeFailed is written when a phase returns an error.
	OutcomeFailed Outcome = "failed"
	// OutcomeRefused is written when srdm declined by contract — an
	// unclassified path, a precondition that did not hold. Distinct from
	// OutcomeFailed so operators can tell "srdm said no" from "srdm broke".
	OutcomeRefused Outcome = "refused"
)

// Record is one journal event.
//
// The field set is closed on purpose. A record never embeds a Profile or any
// other foreign struct, so a credential cannot arrive by accident through a
// field nobody thought about; Fields is string-to-string and passes the
// key denylist.
type Record struct {
	SchemaVersion int               `json:"schema_version"`
	Time          time.Time         `json:"time"`
	OperationID   string            `json:"operation_id"`
	Kind          string            `json:"kind"`
	Phase         string            `json:"phase"`
	Outcome       Outcome           `json:"outcome"`
	Message       string            `json:"message,omitempty"`
	Fields        map[string]string `json:"fields,omitempty"`
	Error         string            `json:"error,omitempty"`
}

// Operation is the durable per-operation record: the accumulated events of
// one operation, rewritten atomically as it progresses.
type Operation struct {
	SchemaVersion int       `json:"schema_version"`
	ID            string    `json:"id"`
	Kind          string    `json:"kind"`
	Started       time.Time `json:"started"`
	Updated       time.Time `json:"updated"`
	Outcome       Outcome   `json:"outcome"`
	Events        []Record  `json:"events"`
}

// Journal writes to all three sinks.
type Journal struct {
	dir      string
	clock    func() time.Time
	journald journaldSink

	mu     sync.Mutex
	events *os.File
	ops    map[string]*Operation
	scrub  *scrubber
}

// Option configures a Journal.
type Option func(*Journal)

// WithClock injects the timestamp source. Tests use a fixed clock; nothing
// in srdm ever decides a verdict from a timestamp.
func WithClock(f func() time.Time) Option {
	return func(j *Journal) { j.clock = f }
}

// WithJournaldSocket points the journald sink at a specific socket path.
// The empty string disables the sink.
func WithJournaldSocket(path string) Option {
	return func(j *Journal) { j.journald = journaldSink{path: path} }
}

// WithSecrets registers credential values to scrub from every sink.
func WithSecrets(secrets ...string) Option {
	return func(j *Journal) { j.scrub.add(secrets...) }
}

// EventsFile is the JSONL stream's name inside the journal directory.
const EventsFile = "events.jsonl"

// OperationsDir holds one durable record per operation.
const OperationsDir = "operations"

// New opens (creating if needed) a journal rooted at dir.
func New(dir string, opts ...Option) (*Journal, error) {
	j := &Journal{
		dir:      dir,
		clock:    time.Now,
		journald: journaldSink{path: DefaultJournaldSocket},
		ops:      make(map[string]*Operation),
		scrub:    newScrubber(),
	}
	for _, o := range opts {
		o(j)
	}
	if err := os.MkdirAll(filepath.Join(dir, OperationsDir), 0o755); err != nil {
		return nil, fmt.Errorf("journal: create %s: %w", dir, err)
	}
	f, err := os.OpenFile(filepath.Join(dir, EventsFile), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, fmt.Errorf("journal: open events: %w", err)
	}
	j.events = f
	return j, nil
}

// AddSecrets registers further credential values to scrub. Safe to call at
// any time; values already written are not rewritten, which is why callers
// register secrets before the first Emit.
func (j *Journal) AddSecrets(secrets ...string) {
	j.mu.Lock()
	defer j.mu.Unlock()
	j.scrub.add(secrets...)
}

// Close flushes and closes the events stream.
func (j *Journal) Close() error {
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.events == nil {
		return nil
	}
	err := j.events.Close()
	j.events = nil
	return err
}

// Emit scrubs a record and writes it to every sink.
//
// The events stream and the per-operation record are fsync'd before Emit
// returns: the store journals each step *before* executing it, so a record
// that is not durable is a record that cannot explain a crash.
func (j *Journal) Emit(r Record) error {
	j.mu.Lock()
	defer j.mu.Unlock()

	r.SchemaVersion = SchemaVersion
	if r.Time.IsZero() {
		r.Time = j.clock()
	}
	r = j.scrub.record(r)

	line, err := json.Marshal(r)
	if err != nil {
		return fmt.Errorf("journal: marshal record: %w", err)
	}
	line = j.scrub.bytes(line)

	var errs []error
	if j.events != nil {
		if _, err := j.events.Write(append(line, '\n')); err != nil {
			errs = append(errs, fmt.Errorf("journal: write events: %w", err))
		} else if err := j.events.Sync(); err != nil {
			errs = append(errs, fmt.Errorf("journal: sync events: %w", err))
		}
	}
	if err := j.updateOperation(r); err != nil {
		errs = append(errs, err)
	}
	// journald is best effort: a node without a running journald must still
	// get its durable records.
	j.journald.send(r, j.scrub)

	return errors.Join(errs...)
}

func (j *Journal) updateOperation(r Record) error {
	if r.OperationID == "" {
		return nil
	}
	op := j.ops[r.OperationID]
	if op == nil {
		op = &Operation{
			SchemaVersion: SchemaVersion,
			ID:            r.OperationID,
			Kind:          r.Kind,
			Started:       r.Time,
		}
		j.ops[r.OperationID] = op
	}
	op.Updated = r.Time
	op.Outcome = r.Outcome
	op.Events = append(op.Events, r)

	b, err := json.MarshalIndent(op, "", "  ")
	if err != nil {
		return fmt.Errorf("journal: marshal operation %s: %w", r.OperationID, err)
	}
	b = j.scrub.bytes(b)
	path := filepath.Join(j.dir, OperationsDir, r.OperationID+".json")
	return fsx.WriteFileSync(path, append(b, '\n'), 0o644)
}

// LoadOperation reads a durable operation record back.
func LoadOperation(dir, opID string) (*Operation, error) {
	b, err := os.ReadFile(filepath.Join(dir, OperationsDir, opID+".json"))
	if err != nil {
		return nil, err
	}
	var op Operation
	if err := json.Unmarshal(b, &op); err != nil {
		return nil, fmt.Errorf("journal: decode operation %s: %w", opID, err)
	}
	return &op, nil
}

// ReadEvents reads the whole events stream back, in order.
func ReadEvents(dir string) ([]Record, error) {
	b, err := os.ReadFile(filepath.Join(dir, EventsFile))
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	lines := splitLines(b)
	var out []Record
	for i, line := range lines {
		if len(line) == 0 {
			continue
		}
		var r Record
		if err := json.Unmarshal(line, &r); err != nil {
			// A crash between the write and the fsync can leave the final
			// record torn. Everything before it is still evidence, and
			// throwing it away would lose exactly the history that explains
			// the crash. Anything earlier in the stream is real corruption,
			// not a torn write, and is reported.
			if i == len(lines)-1 {
				break
			}
			return nil, fmt.Errorf("journal: decode event %d of %d: %w", i+1, len(lines), err)
		}
		out = append(out, r)
	}
	return out, nil
}

// ListOperations returns every operation id with a durable record, sorted.
func ListOperations(dir string) ([]string, error) {
	entries, err := os.ReadDir(filepath.Join(dir, OperationsDir))
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var out []string
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || filepath.Ext(name) != ".json" {
			continue
		}
		out = append(out, name[:len(name)-len(".json")])
	}
	sort.Strings(out)
	return out, nil
}

func splitLines(b []byte) [][]byte {
	var out [][]byte
	start := 0
	for i, c := range b {
		if c == '\n' {
			out = append(out, b[start:i])
			start = i + 1
		}
	}
	if start < len(b) {
		out = append(out, b[start:])
	}
	return out
}
