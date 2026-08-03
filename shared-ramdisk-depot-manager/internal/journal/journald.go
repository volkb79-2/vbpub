package journal

import (
	"bytes"
	"encoding/binary"
	"net"
	"sort"
	"strings"
)

// DefaultJournaldSocket is systemd's native datagram socket.
const DefaultJournaldSocket = "/run/systemd/journal/socket"

// syslog priorities, as journald expects them.
const (
	priErr     = 3
	priWarning = 4
	priInfo    = 6
)

// journaldSink writes structured events in systemd's native journal
// protocol. It is deliberately best effort: a node whose journald is not
// running still gets the durable records, which are the ones srdm reasons
// from.
type journaldSink struct {
	path string
}

func (s journaldSink) send(r Record, sc *scrubber) {
	if s.path == "" {
		return
	}
	conn, err := net.Dial("unixgram", s.path)
	if err != nil {
		return
	}
	defer conn.Close()
	payload := s.encode(r, sc)
	// A datagram too large for the socket buffer would need a memfd
	// hand-off; srdm's records are far below that, and dropping a
	// best-effort duplicate of a durable record is the right failure.
	_, _ = conn.Write(payload)
}

func (s journaldSink) encode(r Record, sc *scrubber) []byte {
	var buf bytes.Buffer

	priority := priInfo
	switch r.Outcome {
	case OutcomeFailed:
		priority = priErr
	case OutcomeRefused:
		priority = priWarning
	}

	writeField(&buf, "MESSAGE", journaldMessage(r))
	writeField(&buf, "PRIORITY", itoa(priority))
	writeField(&buf, "SYSLOG_IDENTIFIER", "srdm")
	writeField(&buf, "SRDM_SCHEMA_VERSION", itoa(r.SchemaVersion))
	writeField(&buf, "SRDM_OPERATION_ID", r.OperationID)
	writeField(&buf, "SRDM_KIND", r.Kind)
	writeField(&buf, "SRDM_PHASE", r.Phase)
	writeField(&buf, "SRDM_OUTCOME", string(r.Outcome))
	if r.Error != "" {
		writeField(&buf, "SRDM_ERROR", r.Error)
	}

	// Field names are normalized into journald's [A-Z0-9_] namespace and
	// prefixed, so a profile-supplied key can never collide with a
	// journald-reserved one such as MESSAGE or _PID.
	keys := make([]string, 0, len(r.Fields))
	for k := range r.Fields {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		v := r.Fields[k]
		if DeniedKey(k) {
			v = Redacted
		}
		writeField(&buf, "SRDM_F_"+normalizeFieldName(k), v)
	}

	return sc.bytes(buf.Bytes())
}

func journaldMessage(r Record) string {
	var sb strings.Builder
	sb.WriteString(r.Kind)
	if r.Phase != "" {
		sb.WriteString(" ")
		sb.WriteString(r.Phase)
	}
	sb.WriteString(" ")
	sb.WriteString(string(r.Outcome))
	if r.Message != "" {
		sb.WriteString(": ")
		sb.WriteString(r.Message)
	}
	return sb.String()
}

// writeField appends one field in the native protocol. A value containing a
// newline must use the binary form, otherwise the newline would terminate
// the field and the remainder would be parsed as a new one.
func writeField(buf *bytes.Buffer, key, value string) {
	if value == "" {
		return
	}
	if !strings.ContainsRune(value, '\n') {
		buf.WriteString(key)
		buf.WriteByte('=')
		buf.WriteString(value)
		buf.WriteByte('\n')
		return
	}
	buf.WriteString(key)
	buf.WriteByte('\n')
	var size [8]byte
	binary.LittleEndian.PutUint64(size[:], uint64(len(value)))
	buf.Write(size[:])
	buf.WriteString(value)
	buf.WriteByte('\n')
}

// normalizeFieldName maps an arbitrary key into journald's permitted field
// namespace: uppercase ASCII letters, digits and underscore.
func normalizeFieldName(k string) string {
	var sb strings.Builder
	for _, r := range strings.ToUpper(k) {
		switch {
		case r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
			sb.WriteRune(r)
		default:
			sb.WriteByte('_')
		}
	}
	out := sb.String()
	if out == "" {
		return "UNNAMED"
	}
	// A leading digit is not a legal journald field name.
	if out[0] >= '0' && out[0] <= '9' {
		return "F" + out
	}
	return out
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}
