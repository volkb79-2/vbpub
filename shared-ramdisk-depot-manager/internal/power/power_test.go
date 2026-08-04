package power

import (
	"strings"
	"testing"
)

func TestRefusalErrorMessageNamesPreconditionDetailAndFix(t *testing.T) {
	err := &RefusalError{Precondition: "unconfigured", Detail: "wings.api_url is not set", Fix: "set --wings-api-url"}
	msg := err.Error()
	for _, want := range []string{"unconfigured", "wings.api_url is not set", "set --wings-api-url"} {
		if !strings.Contains(msg, want) {
			t.Errorf("RefusalError.Error() = %q, want it to contain %q", msg, want)
		}
	}
	if !IsRefusal(err) {
		t.Error("IsRefusal does not recognize a *RefusalError")
	}
}

func TestIsRefusalRejectsAPlainError(t *testing.T) {
	if IsRefusal(errPlain("boom")) {
		t.Error("IsRefusal accepted a plain error")
	}
}

type errPlain string

func (e errPlain) Error() string { return string(e) }

func TestSettleErrorMessageNamesServerWantGotAndTimeout(t *testing.T) {
	err := &SettleError{ServerID: "server-a", Want: StateRunning, Got: StateStarting, Timeout: "5m0s"}
	msg := err.Error()
	for _, want := range []string{"server-a", "running", "starting", "5m0s"} {
		if !strings.Contains(msg, want) {
			t.Errorf("SettleError.Error() = %q, want it to contain %q", msg, want)
		}
	}
}
