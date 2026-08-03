package journal

import (
	"bytes"
	"sort"
	"strings"
)

// Redacted replaces any credential the scrubber removes.
const Redacted = "[redacted]"

// MinSecretLength is the shortest value the scrubber will substring-match.
//
// Below this a "secret" collides with ordinary content — scrubbing "ab"
// would corrupt every record that happens to contain it. Profiles reject
// shorter credentials (profile.Validate) so this bound never silently
// downgrades to "leaked".
const MinSecretLength = 4

// deniedKeySubstrings are the credential-shaped field names. A Fields key
// containing any of them has its value replaced wholesale, whether or not
// the value was ever registered as a secret — so a credential that reaches
// the journal through a path nobody anticipated still does not land.
var deniedKeySubstrings = []string{
	"auth",
	"credential",
	"passwd",
	"password",
	"private",
	"secret",
	"token",
}

// deniedKeyExact catches short names that would over-match as substrings.
var deniedKeyExact = map[string]bool{
	"key":      true,
	"login":    true,
	"pass":     true,
	"pw":       true,
	"session":  true,
	"cookie":   true,
	"bearer":   true,
	"apikey":   true,
	"api_key":  true,
	"identity": true,
}

type scrubber struct {
	secrets []string
}

func newScrubber() *scrubber { return &scrubber{} }

// add registers secret values. Longest first, so a secret that contains
// another is replaced before its substring is.
func (s *scrubber) add(vals ...string) {
	for _, v := range vals {
		if len(v) < MinSecretLength {
			continue
		}
		if !contains(s.secrets, v) {
			s.secrets = append(s.secrets, v)
		}
	}
	sort.SliceStable(s.secrets, func(i, j int) bool {
		return len(s.secrets[i]) > len(s.secrets[j])
	})
}

func contains(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}

// DeniedKey reports whether a field name is credential-shaped.
func DeniedKey(key string) bool {
	lower := strings.ToLower(strings.TrimSpace(key))
	if deniedKeyExact[lower] {
		return true
	}
	for _, sub := range deniedKeySubstrings {
		if strings.Contains(lower, sub) {
			return true
		}
	}
	return false
}

// record scrubs a record structurally: credential-shaped keys lose their
// values, and every remaining string loses any registered secret.
func (s *scrubber) record(r Record) Record {
	r.Message = s.string(r.Message)
	r.Error = s.string(r.Error)
	r.Phase = s.string(r.Phase)
	r.Kind = s.string(r.Kind)
	r.OperationID = s.string(r.OperationID)
	if len(r.Fields) == 0 {
		return r
	}
	out := make(map[string]string, len(r.Fields))
	for k, v := range r.Fields {
		if DeniedKey(k) {
			out[k] = Redacted
			continue
		}
		out[k] = s.string(v)
	}
	r.Fields = out
	return r
}

// string replaces every registered secret in v.
func (s *scrubber) string(v string) string {
	if v == "" || len(s.secrets) == 0 {
		return v
	}
	for _, sec := range s.secrets {
		v = strings.ReplaceAll(v, sec, Redacted)
	}
	return v
}

// bytes is the final pass over already-serialized output. The structural
// pass above is the real defense; this catches a secret that survived JSON
// encoding in a field a future change adds.
func (s *scrubber) bytes(b []byte) []byte {
	if len(s.secrets) == 0 {
		return b
	}
	for _, sec := range s.secrets {
		b = bytes.ReplaceAll(b, []byte(sec), []byte(Redacted))
		// A secret containing characters JSON escapes (a quote, a backslash)
		// appears escaped in the encoded form; match that shape too.
		if esc := jsonEscape(sec); esc != sec {
			b = bytes.ReplaceAll(b, []byte(esc), []byte(Redacted))
		}
	}
	return b
}

// jsonEscape renders s the way encoding/json would inside a string literal,
// without the surrounding quotes.
func jsonEscape(s string) string {
	var sb strings.Builder
	for _, r := range s {
		switch r {
		case '"':
			sb.WriteString(`\"`)
		case '\\':
			sb.WriteString(`\\`)
		case '\n':
			sb.WriteString(`\n`)
		case '\r':
			sb.WriteString(`\r`)
		case '\t':
			sb.WriteString(`\t`)
		default:
			sb.WriteRune(r)
		}
	}
	return sb.String()
}
