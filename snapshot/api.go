package snapshot

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// Snapshot is the truncated view: repo tree + git history + traces.
type Snapshot struct {
	CutoffTime time.Time   `json:"cutoff_time"`
	CutoffKind string      `json:"cutoff_kind"`
	RepoSHA    string      `json:"repo_sha"`
	Commits    []Commit    `json:"commits"`
	Files      []FileEntry `json:"files"`
	Traces     []TraceFile `json:"traces"`
}

// Options configures SnapshotRepo.
type Options struct {
	// TraceDir wires a DirTraceProvider; ignored when Provider is set.
	TraceDir string
	// Provider overrides trace collection (default: dir or stub).
	Provider TraceProvider
	// Peasant resolves PR cutoffs (peasant binary).
	Peasant PeasantClient
	// PRStartOverride bypasses the binary (tests / offline).
	PRStartOverride string
}

// SnapshotRepo builds the snapshot for an arbitrary repo.
//
// Acceptance (issue #2):
//   - date cutoff: no commit/file/trace event after the date (inclusive).
//   - PR cutoff: nothing at or after the PR start (exclusive).
//   - deterministic: same call -> same snapshot (sorted + UTC).
func SnapshotRepo(repoPath string, cutoff Cutoff, opts Options) (*Snapshot, error) {
	if _, err := os.Stat(repoPath); err != nil {
		return nil, fmt.Errorf("snapshot: repo not found: %s", repoPath)
	}
	cutoffTime, err := cutoff.Resolve(opts.Peasant, opts.PRStartOverride)
	if err != nil {
		return nil, err
	}
	exclusive := cutoff.Kind == CutoffPR

	commits, err := ListHistory(repoPath, cutoffTime)
	if err != nil {
		return nil, err
	}
	if exclusive {
		kept := commits[:0]
		for _, c := range commits {
			if c.CommitterTime.Before(cutoffTime) {
				kept = append(kept, c)
			}
		}
		commits = kept
	} else {
		kept := commits[:0]
		for _, c := range commits {
			if !c.CommitterTime.After(cutoffTime) {
				kept = append(kept, c)
			}
		}
		commits = kept
	}
	if len(commits) == 0 {
		if _, err := ResolveSHABefore(repoPath, cutoffTime); err != nil {
			return nil, err
		}
		return nil, fmt.Errorf("snapshot: no commit satisfies cutoff %s",
			cutoffTime.UTC().Format(time.RFC3339))
	}
	// Newest satisfying commit owns the tree, so files obey the cutoff.
	sha := commits[len(commits)-1].SHA
	files, err := ListTree(repoPath, sha)
	if err != nil {
		return nil, err
	}

	provider := opts.Provider
	if provider == nil {
		if opts.TraceDir != "" {
			provider = DirTraceProvider{Root: opts.TraceDir}
		} else {
			provider = StubTraceProvider{}
		}
	}
	traces, err := provider.ListFiles(cutoffTime)
	if err != nil {
		return nil, err
	}
	kept := traces[:0]
	for _, t := range traces {
		if exclusive {
			if t.EventTime.Before(cutoffTime) {
				kept = append(kept, t)
			}
		} else if !t.EventTime.After(cutoffTime) {
			kept = append(kept, t)
		}
	}
	traces = kept
	if traces == nil {
		traces = []TraceFile{}
	}
	if commits == nil {
		commits = []Commit{}
	}
	if files == nil {
		files = []FileEntry{}
	}

	return &Snapshot{
		CutoffTime: cutoffTime.UTC(), CutoffKind: cutoff.Kind,
		RepoSHA: sha, Commits: commits, Files: files, Traces: traces,
	}, nil
}

// ManifestHash is the sha256 of the canonical (compact, field-ordered) JSON.
func (s *Snapshot) ManifestHash() (string, error) {
	raw, err := json.Marshal(snapshotJSON(*s))
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:]), nil
}

// snapshotJSON renders with second-precision UTC timestamps; struct field
// order + sorted slices keep output deterministic.
func snapshotJSON(s Snapshot) any {
	iso := func(t time.Time) string {
		return t.UTC().Truncate(time.Second).Format("2006-01-02T15:04:05+00:00")
	}
	type commitJSON struct {
		SHA           string   `json:"sha"`
		Parents       []string `json:"parents"`
		AuthorTime    string   `json:"author_time"`
		CommitterTime string   `json:"committer_time"`
		Subject       string   `json:"subject"`
	}
	type fileJSON struct {
		Path    string `json:"path"`
		BlobSHA string `json:"blob_sha"`
		Mode    string `json:"mode"`
		Size    int64  `json:"size"`
	}
	type traceJSON struct {
		Path      string `json:"path"`
		EventTime string `json:"event_time"`
		Size      int64  `json:"size"`
	}
	out := struct {
		CutoffKind string       `json:"cutoff_kind"`
		CutoffTime string       `json:"cutoff_time"`
		RepoSHA    string       `json:"repo_sha"`
		Commits    []commitJSON `json:"commits"`
		Files      []fileJSON   `json:"files"`
		Traces     []traceJSON  `json:"traces"`
	}{
		CutoffKind: s.CutoffKind, CutoffTime: iso(s.CutoffTime), RepoSHA: s.RepoSHA,
	}
	for _, c := range s.Commits {
		parents := c.Parents
		if parents == nil {
			parents = []string{}
		}
		out.Commits = append(out.Commits, commitJSON{
			SHA: c.SHA, Parents: parents,
			AuthorTime: iso(c.AuthorTime), CommitterTime: iso(c.CommitterTime),
			Subject: c.Subject,
		})
	}
	for _, f := range s.Files {
		out.Files = append(out.Files, fileJSON{
			Path: f.Path, BlobSHA: f.BlobSHA, Mode: f.Mode, Size: f.Size,
		})
	}
	for _, t := range s.Traces {
		out.Traces = append(out.Traces, traceJSON{
			Path: t.Path, EventTime: iso(t.EventTime), Size: t.Size,
		})
	}
	if out.Commits == nil {
		out.Commits = []commitJSON{}
	}
	if out.Files == nil {
		out.Files = []fileJSON{}
	}
	if out.Traces == nil {
		out.Traces = []traceJSON{}
	}
	return out
}

// CLIPayload returns the snapshot JSON with manifest_sha256 attached,
// for stdout output.
func CLIPayload(s *Snapshot) (map[string]any, error) {
	hash, err := s.ManifestHash()
	if err != nil {
		return nil, err
	}
	raw, err := json.Marshal(snapshotJSON(*s))
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	m["manifest_sha256"] = hash
	return m, nil
}

// WriteSnapshot writes history.json (with manifest_sha256) into outDir.
func WriteSnapshot(s *Snapshot, outDir string) (string, error) {
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return "", err
	}
	hash, err := s.ManifestHash()
	if err != nil {
		return "", err
	}
	payload := snapshotJSON(*s)
	// Re-marshal as a generic map to attach the manifest field deterministically.
	raw, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return "", err
	}
	m["manifest_sha256"] = hash
	pretty, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return "", err
	}
	pretty = append(pretty, '\n')
	path := filepath.Join(outDir, "history.json")
	if err := os.WriteFile(path, pretty, 0o644); err != nil {
		return "", err
	}
	return path, nil
}

// Materialize writes history.json + repo/ tree (+ traces/ when traceRoot != "").
func Materialize(repoPath string, s *Snapshot, outDir, traceRoot string) (string, error) {
	path, err := WriteSnapshot(s, outDir)
	if err != nil {
		return "", err
	}
	repoDir := filepath.Join(outDir, "repo")
	if err := os.MkdirAll(repoDir, 0o755); err != nil {
		return "", err
	}
	if err := MaterializeTree(repoPath, s.RepoSHA, repoDir); err != nil {
		return "", err
	}
	if traceRoot != "" {
		if err := MaterializeTraces(traceRoot, s.Traces, filepath.Join(outDir, "traces")); err != nil {
			return "", err
		}
	}
	return path, nil
}
