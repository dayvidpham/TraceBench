// Package snapshot builds a stable, truncated view of a repo:
//
//	full history -> cutoff (date or PR start) -> snapshot (repo tree, trace, git history)
package snapshot

import (
	"fmt"
	"strings"
	"time"
)

// Cutoff kinds.
const (
	CutoffDate = "date"
	CutoffPR   = "pr"
)

// Cutoff is either an absolute date (inclusive) or a PR number whose start
// time is resolved via the peasant binary (exclusive: nothing at/after start).
type Cutoff struct {
	Kind     string
	Date     time.Time
	PRNumber int
}

// ByDate builds an inclusive date cutoff.
func ByDate(v string) (Cutoff, error) {
	t, err := ParseTime(v)
	if err != nil {
		return Cutoff{}, err
	}
	return Cutoff{Kind: CutoffDate, Date: t}, nil
}

// ByPR builds a PR cutoff (resolved to the PR start, exclusive).
func ByPR(n int) (Cutoff, error) {
	if n <= 0 {
		return Cutoff{}, fmt.Errorf("snapshot: PR number must be positive, got %d", n)
	}
	return Cutoff{Kind: CutoffPR, PRNumber: n}, nil
}

// ParseTime parses ISO-8601, normalizing to tz-aware UTC.
// Accepts a trailing "Z" and naive timestamps (assumed UTC).
func ParseTime(v string) (time.Time, error) {
	text := strings.TrimSpace(v)
	if strings.HasSuffix(text, "Z") {
		text = text[:len(text)-1] + "+00:00"
	}
	// Try full RFC3339 first, then common variants.
	layouts := []string{
		time.RFC3339,
		"2006-01-02T15:04:05-07:00",
		"2006-01-02T15:04:05",
		"2006-01-02 15:04:05-07:00",
		"2006-01-02",
	}
	var last error
	for _, l := range layouts {
		t, err := time.Parse(l, text)
		if err == nil {
			return t.UTC(), nil
		}
		last = err
	}
	return time.Time{}, fmt.Errorf("snapshot: unparsable time %q: %v", v, last)
}

// Resolve maps a Cutoff to an absolute UTC timestamp. PR cutoffs need either
// a PeasantClient (peasant binary) or a prStartOverride ISO timestamp.
func (c Cutoff) Resolve(peasant PeasantClient, prStartOverride string) (time.Time, error) {
	switch c.Kind {
	case CutoffDate:
		return c.Date.UTC(), nil
	case CutoffPR:
		if prStartOverride != "" {
			t, err := ParseTime(prStartOverride)
			if err != nil {
				return time.Time{}, err
			}
			return t.UTC(), nil
		}
		if peasant == nil {
			return time.Time{}, fmt.Errorf(
				"snapshot: PR cutoff needs a PeasantClient (peasant binary) or prStartOverride")
		}
		t, err := peasant.PRStart(c.PRNumber)
		if err != nil {
			return time.Time{}, err
		}
		return t.UTC(), nil
	default:
		return time.Time{}, fmt.Errorf("snapshot: unknown cutoff kind %q", c.Kind)
	}
}
