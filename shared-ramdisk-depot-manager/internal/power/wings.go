package power

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"
)

// WingsDriver stops, starts and reads the status and log tail of servers
// through WINGS' OWN node API — not the Pterodactyl Panel's.
//
// The Panel can be unreachable while the node it manages keeps running; an
// update orchestrator that depended on the Panel anyway would be a needless
// second point of failure for the one thing it absolutely has to be able to
// do. Modeled on wings-cgroups/wingsctl/wingsctl.py, which exists for the
// same reason and against the same API (decisions.md D-032):
//
//	POST /api/servers/<uuid>/power   {"action":"start"|"stop"|"restart"|"kill"}
//	GET  /api/servers/<uuid>         -> {"state": "running"|"offline"|...}
//	GET  /api/servers/<uuid>/logs?size=N -> {"data": [line, line, ...]}
//	GET  /api/servers                -> list, for a reachability check
type WingsDriver struct {
	baseURL string
	bearers []string
	client  *http.Client

	stopTimeout  time.Duration
	startTimeout time.Duration
	pollInterval time.Duration
}

// WingsConfig is what WingsDriver needs. Mirrors config.Wings's node-API
// fields field for field, so this package carries no dependency on
// internal/config.
type WingsConfig struct {
	APIURL       string
	APIInsecure  bool
	ConfigPath   string // Wings' own config.yml — read for token/token_id
	StopTimeout  time.Duration
	StartTimeout time.Duration
	PollInterval time.Duration
}

// NewWingsDriver returns a driver, or a RefusalError naming what to set.
//
// Unconfigured refuses rather than guesses (D-021's discipline): a rolling
// update that could not tell whether it can stop a server is not a safer
// update for having tried.
func NewWingsDriver(cfg WingsConfig) (*WingsDriver, error) {
	if cfg.APIURL == "" {
		return nil, &RefusalError{
			Precondition: PreconditionUnconfigured,
			Detail:       "wings.api_url is not set",
			Fix:          "set --wings-api-url (or wings.api_url in config) to Wings' node API address",
		}
	}
	bearers, err := readNodeTokens(cfg.ConfigPath)
	if err != nil {
		return nil, err
	}
	stop, start, poll := cfg.StopTimeout, cfg.StartTimeout, cfg.PollInterval
	if stop <= 0 {
		stop = 2 * time.Minute
	}
	if start <= 0 {
		start = 5 * time.Minute
	}
	if poll <= 0 {
		poll = 2 * time.Second
	}
	return &WingsDriver{
		baseURL: strings.TrimSuffix(cfg.APIURL, "/"),
		bearers: bearers,
		client: &http.Client{
			Timeout: 30 * time.Second,
			// InsecureSkipVerify only when the operator explicitly asked
			// for it (wings.api_insecure) — off by default; see D-032.
			Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: cfg.APIInsecure}},
		},
		stopTimeout: stop, startTimeout: start, pollInterval: poll,
	}, nil
}

// Name identifies the driver in records and journal entries.
func (d *WingsDriver) Name() string { return "wings" }

// Bearers returns the bearer candidates this driver authenticates with —
// read once at construction from Wings' config.yml.
//
// THE TOKEN IS A SECRET. This exists so a caller can register it with
// journal.WithSecrets / journal.AddSecrets before doing anything else —
// see decisions.md D-032. It must never be logged, put in an error message,
// or written to a journal field directly.
func (d *WingsDriver) Bearers() []string {
	out := make([]string, len(d.bearers))
	copy(out, d.bearers)
	return out
}

// tokenLine matches a TOP-LEVEL (no leading whitespace) "token:" or
// "token_id:" key in Wings' config.yml — the same anchoring
// wingsctl.py's `^token:\s*(\S+)\s*$` regex uses, so an indented,
// differently-scoped key elsewhere in the document is never mistaken for
// the node's own credential.
var (
	tokenLine   = regexp.MustCompile(`^token:\s*(\S+)\s*$`)
	tokenIDLine = regexp.MustCompile(`^token_id:\s*(\S+)\s*$`)
)

// readNodeTokens reads Bearer candidates from Wings' config.yml, bare token
// first — Wings v1 authorizes the node API against the bare `token` value;
// some builds expect the compound `token_id.token` form. Trying both,
// in that order, is wingsctl's own fallback and is copied rather than
// reinvented.
//
// THE TOKEN IS A SECRET. Every caller of this function must register every
// returned value with journal.WithSecrets before doing anything else with
// it — see decisions.md D-032 and
// cmd/srdm.TestNewOpEnvRegistersTheWingsTokenWithTheJournal.
func readNodeTokens(path string) ([]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, &RefusalError{
			Precondition: PreconditionUnconfigured,
			Detail:       fmt.Sprintf("cannot read %s: %v", path, err),
			Fix: "run as a user that can read Wings' config.yml (usually root), or point " +
				"--wings-config at it",
		}
	}
	defer f.Close()

	var token, tokenID string
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if m := tokenLine.FindStringSubmatch(line); m != nil {
			token = strings.Trim(m[1], `'"`)
		}
		if m := tokenIDLine.FindStringSubmatch(line); m != nil {
			tokenID = strings.Trim(m[1], `'"`)
		}
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("power: reading %s: %w", path, err)
	}
	if token == "" {
		return nil, &RefusalError{
			Precondition: PreconditionUnconfigured,
			Detail:       fmt.Sprintf("%s has no top-level token: key", path),
			Fix: "confirm Wings' config.yml has a top-level `token:` key — srdm reads it " +
				"the same way wingsctl.py does",
		}
	}
	bearers := []string{token}
	if tokenID != "" {
		bearers = append(bearers, tokenID+"."+token)
	}
	return bearers, nil
}

// do sends one request, trying each bearer candidate in order until one
// gets a non-auth answer — mirroring wingsctl's request(): a 401/403 means
// "try the next bearer shape", anything else is a real answer even if it is
// itself an error.
func (d *WingsDriver) do(ctx context.Context, method, path string, reqBody any) (int, []byte, error) {
	var payload []byte
	if reqBody != nil {
		b, err := json.Marshal(reqBody)
		if err != nil {
			return 0, nil, err
		}
		payload = b
	}

	var lastStatus int
	var lastBody []byte
	for _, bearer := range d.bearers {
		var reader io.Reader
		if payload != nil {
			reader = bytes.NewReader(payload)
		}
		req, err := http.NewRequestWithContext(ctx, method, d.baseURL+path, reader)
		if err != nil {
			return 0, nil, err
		}
		req.Header.Set("Authorization", "Bearer "+bearer)
		req.Header.Set("Accept", "application/json")
		if payload != nil {
			req.Header.Set("Content-Type", "application/json")
		}
		resp, err := d.client.Do(req)
		if err != nil {
			return 0, nil, fmt.Errorf("power: contacting wings: %w", err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
			lastStatus, lastBody = resp.StatusCode, body
			continue
		}
		return resp.StatusCode, body, nil
	}
	return lastStatus, lastBody, nil
}

type wingsServerDetail struct {
	State string `json:"state"`
}

// Status reports a server's current power state.
func (d *WingsDriver) Status(ctx context.Context, serverID string) (State, error) {
	status, body, err := d.do(ctx, http.MethodGet, "/api/servers/"+serverID, nil)
	if err != nil {
		return StateUnknown, err
	}
	if status != http.StatusOK {
		return StateUnknown, fmt.Errorf("power: wings answered %d for %s: %s",
			status, serverID, strings.TrimSpace(string(body)))
	}
	var out wingsServerDetail
	if err := json.Unmarshal(body, &out); err != nil {
		return StateUnknown, fmt.Errorf("power: parsing wings' response for %s: %w", serverID, err)
	}
	return parseState(out.State), nil
}

func parseState(s string) State {
	switch State(s) {
	case StateRunning, StateOffline, StateStarting, StateStopping:
		return State(s)
	default:
		return StateUnknown
	}
}

func (d *WingsDriver) signal(ctx context.Context, serverID, action string) error {
	status, body, err := d.do(ctx, http.MethodPost, "/api/servers/"+serverID+"/power",
		map[string]string{"action": action})
	if err != nil {
		return err
	}
	// Power requests return 202 immediately — the egg's stop/start sequence
	// runs after, which is exactly what settle polls for.
	switch status {
	case http.StatusOK, http.StatusAccepted, http.StatusNoContent:
		return nil
	default:
		return fmt.Errorf("power: wings answered %d sending %q to %s: %s",
			status, action, serverID, strings.TrimSpace(string(body)))
	}
}

// settle polls Status until it reports want, or returns a *SettleError once
// timeout has elapsed.
func (d *WingsDriver) settle(ctx context.Context, serverID string, want State, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	var last State
	for {
		st, err := d.Status(ctx, serverID)
		if err != nil {
			return err
		}
		last = st
		if st == want {
			return nil
		}
		if time.Now().After(deadline) {
			return &SettleError{ServerID: serverID, Want: want, Got: last, Timeout: timeout.String()}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(d.pollInterval):
		}
	}
}

// Stop asks a server to stop and blocks until it has settled offline.
//
// "stop" (not "kill") sets the DESIRED state to offline, so Wings will not
// crash-restart the server underneath the swap this package exists to
// make safe — the same distinction wingsctl's own doc comment makes.
func (d *WingsDriver) Stop(ctx context.Context, serverID string) error {
	if err := d.signal(ctx, serverID, "stop"); err != nil {
		return err
	}
	return d.settle(ctx, serverID, StateOffline, d.stopTimeout)
}

// Start asks a server to start and blocks until it is running.
func (d *WingsDriver) Start(ctx context.Context, serverID string) error {
	if err := d.signal(ctx, serverID, "start"); err != nil {
		return err
	}
	return d.settle(ctx, serverID, StateRunning, d.startTimeout)
}

type wingsLogs struct {
	Data []string `json:"data"`
}

// DefaultLogTailSize is how many trailing lines Logs asks for by default.
const DefaultLogTailSize = 200

// Logs returns the server's most recent size log lines — WingsLogReadiness's
// evidence source, and also useful on its own for an operator's `doctor`.
func (d *WingsDriver) Logs(ctx context.Context, serverID string, size int) ([]string, error) {
	if size <= 0 {
		size = DefaultLogTailSize
	}
	status, body, err := d.do(ctx, http.MethodGet,
		fmt.Sprintf("/api/servers/%s/logs?size=%d", serverID, size), nil)
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("power: wings answered %d for %s's logs: %s",
			status, serverID, strings.TrimSpace(string(body)))
	}
	var out wingsLogs
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, fmt.Errorf("power: parsing wings' log response for %s: %w", serverID, err)
	}
	return out.Data, nil
}

// Reachable answers `doctor`'s question without touching any one server:
// GET /api/servers, which every node with a token this driver could build
// at all can answer.
func (d *WingsDriver) Reachable(ctx context.Context) error {
	status, body, err := d.do(ctx, http.MethodGet, "/api/servers", nil)
	if err != nil {
		return err
	}
	if status != http.StatusOK {
		return fmt.Errorf("power: wings answered %d listing servers: %s",
			status, strings.TrimSpace(string(body)))
	}
	return nil
}
