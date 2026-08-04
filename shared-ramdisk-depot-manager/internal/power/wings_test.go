package power

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func ctx() context.Context { return context.Background() }

func writeWingsConfig(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yml")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestReadNodeTokensPrefersTheBareTokenFirst(t *testing.T) {
	path := writeWingsConfig(t, "debug: false\ntoken_id: abc123\ntoken: 'secret-value'\napi:\n  port: 443\n")
	bearers, err := readNodeTokens(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(bearers) != 2 || bearers[0] != "secret-value" || bearers[1] != "abc123.secret-value" {
		t.Fatalf("readNodeTokens() = %v", bearers)
	}
}

func TestReadNodeTokensWithNoTokenIDReturnsJustTheBareToken(t *testing.T) {
	path := writeWingsConfig(t, "token: bareonly\n")
	bearers, err := readNodeTokens(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(bearers) != 1 || bearers[0] != "bareonly" {
		t.Fatalf("readNodeTokens() = %v", bearers)
	}
}

// A nested (indented) key of the same name, in some other block, must never
// be mistaken for the node's own top-level credential.
func TestReadNodeTokensIgnoresIndentedKeys(t *testing.T) {
	path := writeWingsConfig(t, "system:\n  sftp:\n    token: not-the-node-token\ntoken: real-token\n")
	bearers, err := readNodeTokens(path)
	if err != nil {
		t.Fatal(err)
	}
	if bearers[0] != "real-token" {
		t.Fatalf("readNodeTokens() picked up an indented key: %v", bearers)
	}
}

func TestReadNodeTokensRefusesWhenTokenIsMissing(t *testing.T) {
	path := writeWingsConfig(t, "debug: false\n")
	if _, err := readNodeTokens(path); !IsRefusal(err) {
		t.Fatalf("want a refusal for a config with no token, got %v", err)
	}
}

// A directory opens successfully (os.Open does not distinguish) but fails
// to scan as text — the scanner's own error path, distinct from "the file
// does not exist".
func TestReadNodeTokensReportsAScanError(t *testing.T) {
	dir := t.TempDir()
	if _, err := readNodeTokens(dir); err == nil {
		t.Fatal("reading a directory as a config file was accepted")
	}
}

func TestReadNodeTokensRefusesWhenTheFileCannotBeRead(t *testing.T) {
	if _, err := readNodeTokens(filepath.Join(t.TempDir(), "does-not-exist.yml")); !IsRefusal(err) {
		t.Fatalf("want a refusal for an unreadable config, got %v", err)
	}
}

func TestNewWingsDriverRefusesWithNoAPIURL(t *testing.T) {
	path := writeWingsConfig(t, "token: t\n")
	if _, err := NewWingsDriver(WingsConfig{ConfigPath: path}); !IsRefusal(err) {
		t.Fatalf("want a refusal with no APIURL, got %v", err)
	}
}

// fakeWings is a minimal Wings node API: it serves status, power and logs,
// and — the property this whole design exists for — rejects the FIRST
// bearer it is tried with (as if the bare token were the wrong shape) so
// the fallback-to-token_id.token path is exercised for real.
type fakeWings struct {
	t          *testing.T
	goodBearer string
	states     map[string]string
	logs       map[string][]string
	signals    []string

	// Failure injection, all zero/false by default (a healthy node).
	statusCode      int  // override for GET /api/servers/<id>; 0 = 200
	statusMalformed bool // write unparsable JSON for GET /api/servers/<id>
	powerCode       int  // override for POST .../power; 0 = 202
	logsCode        int  // override for GET .../logs; 0 = 200
	logsMalformed   bool
	listCode        int // override for GET /api/servers; 0 = 200
}

func newFakeWings(t *testing.T, goodBearer string) (*fakeWings, *httptest.Server) {
	t.Helper()
	f := &fakeWings{t: t, goodBearer: goodBearer, states: map[string]string{}, logs: map[string][]string{}}
	mux := http.NewServeMux()
	handler := func(w http.ResponseWriter, r *http.Request) {
		if !f.authorize(w, r) {
			return
		}
		if r.URL.Path == "/api/servers" {
			code := f.listCode
			if code == 0 {
				code = http.StatusOK
			}
			w.WriteHeader(code)
			_, _ = w.Write([]byte(`{"data":[]}`))
			return
		}
		rest := strings.TrimPrefix(r.URL.Path, "/api/servers/")
		id, action, _ := strings.Cut(rest, "/")
		switch {
		case action == "" && r.Method == http.MethodGet:
			code := f.statusCode
			if code == 0 {
				code = http.StatusOK
			}
			w.WriteHeader(code)
			if f.statusMalformed {
				_, _ = w.Write([]byte(`{not json`))
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]string{"state": f.states[id]})
		case action == "power" && r.Method == http.MethodPost:
			var body struct {
				Action string `json:"action"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			f.signals = append(f.signals, id+":"+body.Action)
			code := f.powerCode
			if code == 0 {
				code = http.StatusAccepted
			}
			w.WriteHeader(code)
		case strings.HasPrefix(action, "logs") && r.Method == http.MethodGet:
			code := f.logsCode
			if code == 0 {
				code = http.StatusOK
			}
			w.WriteHeader(code)
			if f.logsMalformed {
				_, _ = w.Write([]byte(`{not json`))
				return
			}
			_ = json.NewEncoder(w).Encode(map[string][]string{"data": f.logs[id]})
		default:
			http.NotFound(w, r)
		}
	}
	mux.HandleFunc("/api/servers", handler)
	mux.HandleFunc("/api/servers/", handler)
	ts := httptest.NewServer(mux)
	t.Cleanup(ts.Close)
	return f, ts
}

func (f *fakeWings) authorize(w http.ResponseWriter, r *http.Request) bool {
	got := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	if got != f.goodBearer {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"error":"unauthorized"}`))
		return false
	}
	return true
}

func TestStatusFallsBackFromTheBareTokenToTheCompoundOne(t *testing.T) {
	fake, ts := newFakeWings(t, "abc123.secret-value") // only the COMPOUND form is accepted
	fake.states["server-a"] = "running"

	path := writeWingsConfig(t, "token_id: abc123\ntoken: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path, PollInterval: time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	got, err := d.Status(ctx(), "server-a")
	if err != nil {
		t.Fatalf("Status: %v", err)
	}
	if got != StateRunning {
		t.Errorf("Status() = %q, want %q", got, StateRunning)
	}
}

func TestStopSignalsThenPollsUntilOffline(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.states["server-a"] = "offline"
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path, PollInterval: time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	if err := d.Stop(ctx(), "server-a"); err != nil {
		t.Fatalf("Stop: %v", err)
	}
	if len(fake.signals) != 1 || fake.signals[0] != "server-a:stop" {
		t.Errorf("signals sent: %v, want exactly one server-a:stop", fake.signals)
	}
}

func TestStartSignalsThenPollsUntilRunning(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.states["server-a"] = "running"
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path, PollInterval: time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	if err := d.Start(ctx(), "server-a"); err != nil {
		t.Fatalf("Start: %v", err)
	}
	if len(fake.signals) != 1 || fake.signals[0] != "server-a:start" {
		t.Errorf("signals sent: %v, want exactly one server-a:start", fake.signals)
	}
}

func TestStopTimesOutWithASettleError(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.states["server-a"] = "stopping" // never advances
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{
		APIURL: ts.URL, ConfigPath: path,
		StopTimeout: 5 * time.Millisecond, PollInterval: time.Millisecond,
	})
	if err != nil {
		t.Fatal(err)
	}
	err = d.Stop(ctx(), "server-a")
	var settle *SettleError
	if !errors.As(err, &settle) {
		t.Fatalf("want a *SettleError, got %v (%T)", err, err)
	}
	if settle.Want != StateOffline || settle.Got != StateStopping {
		t.Errorf("SettleError = %+v", settle)
	}
}

func TestLogsReturnsTheTail(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.logs["server-a"] = []string{"line one", "line two"}
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	lines, err := d.Logs(ctx(), "server-a", 50)
	if err != nil {
		t.Fatal(err)
	}
	if len(lines) != 2 || lines[1] != "line two" {
		t.Errorf("Logs() = %v", lines)
	}
}

func TestReachableAsksTheServerList(t *testing.T) {
	_, ts := newFakeWings(t, "secret-value")
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	if err := d.Reachable(ctx()); err != nil {
		t.Fatalf("Reachable: %v", err)
	}
}

func TestEveryBearerRejectedIsAFault(t *testing.T) {
	_, ts := newFakeWings(t, "the-real-token") // configured token will never match
	path := writeWingsConfig(t, "token: wrong-token\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := d.Status(ctx(), "server-a"); err == nil {
		t.Fatal("Status succeeded with a token wings never accepted")
	}
}

func TestWingsDriverNameIsStable(t *testing.T) {
	path := writeWingsConfig(t, "token: t\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: "https://127.0.0.1:8080", ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	if d.Name() != "wings" {
		t.Errorf("Name() = %q, want %q", d.Name(), "wings")
	}
}

// Bearers exists so a caller can register the token with the journal's
// scrubber (D-032); it must hand back every candidate and must not let the
// caller mutate the driver's own copy through the returned slice.
func TestBearersReturnsEveryCandidateAsACopy(t *testing.T) {
	path := writeWingsConfig(t, "token_id: abc123\ntoken: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: "https://127.0.0.1:1", ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	got := d.Bearers()
	if len(got) != 2 || got[0] != "secret-value" || got[1] != "abc123.secret-value" {
		t.Fatalf("Bearers() = %v", got)
	}
	got[0] = "mutated"
	if d.Bearers()[0] != "secret-value" {
		t.Error("mutating the returned slice changed the driver's own bearers")
	}
}

func TestStatusOfAnUnrecognizedStateIsUnknown(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.states["server-a"] = "crashed" // not one of the four documented states
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	got, err := d.Status(ctx(), "server-a")
	if err != nil {
		t.Fatal(err)
	}
	if got != StateUnknown {
		t.Errorf("Status() = %q, want %q", got, StateUnknown)
	}
}

func TestStatusFailsOnANonOKResponse(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.statusCode = http.StatusInternalServerError
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := d.Status(ctx(), "server-a"); err == nil {
		t.Fatal("Status succeeded against a 500 response")
	}
}

func TestStatusFailsOnUnparsableJSON(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.statusMalformed = true
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := d.Status(ctx(), "server-a"); err == nil {
		t.Fatal("Status succeeded against an unparsable body")
	}
}

// A power signal Wings rejects outright must fail BEFORE ever polling
// settle — there is nothing to wait for if the stop/start was never
// accepted.
func TestStopFailsWhenThePowerSignalIsRejected(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.powerCode = http.StatusBadRequest
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path, PollInterval: time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	if err := d.Stop(ctx(), "server-a"); err == nil {
		t.Fatal("Stop succeeded despite the power signal being rejected")
	}
	if len(fake.signals) != 1 {
		t.Errorf("signals sent: %v, want exactly one attempt", fake.signals)
	}
}

func TestStartFailsWhenThePowerSignalIsRejected(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.powerCode = http.StatusBadRequest
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path, PollInterval: time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	if err := d.Start(ctx(), "server-a"); err == nil {
		t.Fatal("Start succeeded despite the power signal being rejected")
	}
}

func TestLogsFailsOnANonOKResponse(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.logsCode = http.StatusInternalServerError
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := d.Logs(ctx(), "server-a", 50); err == nil {
		t.Fatal("Logs succeeded against a 500 response")
	}
}

func TestLogsFailsOnUnparsableJSON(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.logsMalformed = true
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := d.Logs(ctx(), "server-a", 50); err == nil {
		t.Fatal("Logs succeeded against an unparsable body")
	}
}

// size <= 0 falls back to DefaultLogTailSize rather than asking Wings for a
// nonsensical or unbounded tail.
func TestLogsDefaultsTheSizeWhenNonPositive(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.logs["server-a"] = []string{"a line"}
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := d.Logs(ctx(), "server-a", 0); err != nil {
		t.Fatal(err)
	}
}

func TestReachableFailsOnANonOKResponse(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.listCode = http.StatusServiceUnavailable
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: ts.URL, ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	if err := d.Reachable(ctx()); err == nil {
		t.Fatal("Reachable succeeded against a 503 response")
	}
}

// A transport-level failure (the node is simply not there) is `do`'s own
// error path, shared by every method built on it — Status, Stop/Start's
// signal, Logs and Reachable all propagate it the same way.
func TestDoFailsOnATransportError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	url := ts.URL
	ts.Close() // nothing is listening here anymore

	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{APIURL: url, ConfigPath: path})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := d.Status(ctx(), "server-a"); err == nil {
		t.Fatal("Status succeeded against a closed listener")
	}
	if err := d.Reachable(ctx()); err == nil {
		t.Fatal("Reachable succeeded against a closed listener")
	}
	if _, err := d.Logs(ctx(), "server-a", 10); err == nil {
		t.Fatal("Logs succeeded against a closed listener")
	}
}

// settle must respect context cancellation rather than spinning until its
// own timeout regardless of the caller giving up first.
func TestSettleStopsWhenTheContextIsCancelled(t *testing.T) {
	fake, ts := newFakeWings(t, "secret-value")
	fake.states["server-a"] = "starting" // never reaches offline
	path := writeWingsConfig(t, "token: secret-value\n")
	d, err := NewWingsDriver(WingsConfig{
		APIURL: ts.URL, ConfigPath: path,
		StopTimeout: time.Hour, PollInterval: time.Millisecond, // would hang without cancellation
	})
	if err != nil {
		t.Fatal(err)
	}
	cctx, cancel := context.WithCancel(ctx())
	go func() {
		time.Sleep(20 * time.Millisecond)
		cancel()
	}()
	if err := d.Stop(cctx, "server-a"); err == nil {
		t.Fatal("Stop reported success after its context was cancelled")
	}
}
