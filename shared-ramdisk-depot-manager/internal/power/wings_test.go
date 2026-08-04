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
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"data":[]}`))
			return
		}
		rest := strings.TrimPrefix(r.URL.Path, "/api/servers/")
		id, action, _ := strings.Cut(rest, "/")
		switch {
		case action == "" && r.Method == http.MethodGet:
			w.WriteHeader(http.StatusOK)
			_ = json.NewEncoder(w).Encode(map[string]string{"state": f.states[id]})
		case action == "power" && r.Method == http.MethodPost:
			var body struct {
				Action string `json:"action"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			f.signals = append(f.signals, id+":"+body.Action)
			w.WriteHeader(http.StatusAccepted)
		case strings.HasPrefix(action, "logs") && r.Method == http.MethodGet:
			w.WriteHeader(http.StatusOK)
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
