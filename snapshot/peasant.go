package snapshot

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// Default contract for the peasant binary (overridable — the CLI is evolving):
//
//	peasant pr show <n> --format json
//
// Stdout must be a JSON object (or {"pr": {...}} / single-element list)
// with one of StartedAtKeys. First match wins, parsed as ISO-8601.
var DefaultStartedAtKeys = []string{
	"started_at", "created_at", "createdAt", "start_time", "startedAt",
}

// PeasantClient resolves PR numbers to PR start timestamps.
type PeasantClient interface {
	PRStart(pr int) (time.Time, error)
	Version() (string, error)
}

// BinaryPeasantClient shells out to the peasant binary.
type BinaryPeasantClient struct {
	Binary          string
	Timeout         time.Duration
	ExpectedVersion string
	// PRShowArgv overrides the default argv template; "{pr}" is substituted.
	PRShowArgv    []string
	StartedAtKeys []string
}

func (b BinaryPeasantClient) timeout() time.Duration {
	if b.Timeout > 0 {
		return b.Timeout
	}
	return 30 * time.Second
}

func (b BinaryPeasantClient) keys() []string {
	if len(b.StartedAtKeys) > 0 {
		return b.StartedAtKeys
	}
	return DefaultStartedAtKeys
}

func (b BinaryPeasantClient) run(argv []string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), b.timeout())
	defer cancel()
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	out, err := cmd.Output()
	if ctx.Err() == context.DeadlineExceeded {
		return "", fmt.Errorf("snapshot: peasant timed out after %s: %s",
			b.timeout(), strings.Join(argv, " "))
	}
	if err != nil {
		if ee, ok := err.(*exec.Error); ok && exec.ErrNotFound != nil && isNotFound(ee) {
			return "", fmt.Errorf("snapshot: peasant binary not found: %q", b.Binary)
		}
		if exit, ok := err.(*exec.ExitError); ok {
			return "", fmt.Errorf("snapshot: peasant failed (exit %d): %s\nstderr: %s",
				exit.ExitCode(), strings.Join(argv, " "),
				truncate(string(exit.Stderr), 2000))
		}
		return "", fmt.Errorf("snapshot: peasant failed: %s: %v", strings.Join(argv, " "), err)
	}
	return string(out), nil
}

func isNotFound(err *exec.Error) bool {
	return strings.Contains(err.Error(), "executable file not found")
}

func truncate(s string, n int) string {
	s = strings.TrimSpace(s)
	if len(s) > n {
		return s[:n]
	}
	return s
}

// Version runs `peasant --version`, failing fast on skew when pinned.
func (b BinaryPeasantClient) Version() (string, error) {
	out, err := b.run([]string{b.Binary, "--version"})
	if err != nil {
		return "", err
	}
	out = strings.TrimSpace(out)
	if b.ExpectedVersion != "" && !strings.Contains(out, b.ExpectedVersion) {
		return "", fmt.Errorf("snapshot: peasant version mismatch: expected %q in %q",
			b.ExpectedVersion, out)
	}
	return out, nil
}

// PRStart resolves a PR number to its start timestamp via the binary.
func (b BinaryPeasantClient) PRStart(pr int) (time.Time, error) {
	if pr <= 0 {
		return time.Time{}, fmt.Errorf("snapshot: invalid PR number: %d", pr)
	}
	template := b.PRShowArgv
	if len(template) == 0 {
		template = []string{b.Binary, "pr", "show", "{pr}", "--format", "json"}
	}
	argv := make([]string, len(template))
	for i, a := range template {
		argv[i] = strings.ReplaceAll(a, "{pr}", fmt.Sprint(pr))
	}
	out, err := b.run(argv)
	if err != nil {
		return time.Time{}, err
	}
	var payload any
	if err := json.Unmarshal([]byte(out), &payload); err != nil {
		return time.Time{}, fmt.Errorf("snapshot: peasant returned non-JSON output: %.500q", out)
	}
	return extractStartedAt(payload, b.keys())
}

func extractStartedAt(payload any, keys []string) (time.Time, error) {
	obj := payload
	if list, ok := obj.([]any); ok {
		if len(list) == 0 {
			return time.Time{}, fmt.Errorf("snapshot: peasant pr show returned empty list")
		}
		obj = list[0]
	}
	m, ok := obj.(map[string]any)
	if !ok {
		return time.Time{}, fmt.Errorf("snapshot: unexpected peasant JSON shape")
	}
	if nested, ok := m["pr"].(map[string]any); ok {
		m = nested
	}
	for _, k := range keys {
		if v, ok := m[k]; ok && v != nil && fmt.Sprint(v) != "" {
			t, err := ParseTime(fmt.Sprint(v))
			if err != nil {
				return time.Time{}, fmt.Errorf(
					"snapshot: peasant returned unparsable time %v for key %q", v, k)
			}
			return t, nil
		}
	}
	// Collect available keys for the error without leaking huge payloads.
	var got []string
	for k := range m {
		got = append(got, k)
		if len(got) >= 8 {
			break
		}
	}
	return time.Time{}, fmt.Errorf(
		"snapshot: peasant JSON has none of the time keys %v; got %v", keys, got)
}

// StubPeasantClient is an in-memory fake for tests / offline use.
type StubPeasantClient struct {
	Starts     map[int]time.Time
	VersionStr string
}

func (s StubPeasantClient) Version() (string, error) {
	if s.VersionStr != "" {
		return s.VersionStr, nil
	}
	return "stub", nil
}

func (s StubPeasantClient) PRStart(pr int) (time.Time, error) {
	t, ok := s.Starts[pr]
	if !ok {
		return time.Time{}, fmt.Errorf("snapshot: stub has no PR %d", pr)
	}
	return t.UTC(), nil
}
