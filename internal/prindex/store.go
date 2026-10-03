package prindex

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

// Index is the persisted result of indexing pull requests and traces.
type Index struct {
	GeneratedAt time.Time            `json:"generated_at"`
	RepoHost    string               `json:"repo_host"`
	Repos       []string             `json:"repos"`
	MergedPRs   []corpus.PullRequest `json:"-"`
	Sessions    []corpus.Session     `json:"-"`
	Traces      []corpus.TraceLink   `json:"-"`
	Commits     []CommitMapping      `json:"-"`
	Summary     Summary              `json:"summary"`
}

// RepoSummary reports coverage of one repository.
type RepoSummary struct {
	Repo           string `json:"repo"`
	MergedPRs      int    `json:"merged_prs"`
	TracedPRs      int    `json:"traced_prs"`
	TracedSessions int    `json:"traced_sessions"`
	Traces         int    `json:"traces"`
}

// Summary reports index-wide coverage.
type Summary struct {
	TotalSessions          int           `json:"total_sessions"`
	SessionsWithKeys       int           `json:"sessions_with_keys"`
	LinkedSessions         int           `json:"linked_sessions"`
	Traces                 int           `json:"traces"`
	CommitHashes           int           `json:"commit_hashes,omitempty"`
	CommitHashesResolved   int           `json:"commit_hashes_resolved,omitempty"`
	CommitHashesUnresolved int           `json:"commit_hashes_unresolved,omitempty"`
	Repos                  []RepoSummary `json:"repos"`
}

// BuildSummary computes coverage counts from traces over the given sessions
// and pull requests. Sessions with no candidate key are counted separately so
// a misconfigured repository host is visible.
func BuildSummary(sessions []corpus.Session, prs []corpus.PullRequest, traces []corpus.TraceLink, extractor *KeyExtractor) Summary {
	byID := make(map[string]corpus.Session, len(sessions))
	for _, s := range sessions {
		byID[s.ID] = s
	}
	withKeys := 0
	for _, s := range sessions {
		if len(extractor.Keys(s, byID)) > 0 {
			withKeys++
		}
	}

	prOwner := make(map[string]string, len(prs))
	for _, pr := range prs {
		prOwner[pr.ID()] = string(pr.Repo)
	}
	type repoCounts struct {
		merged         int
		tracedPRs      map[string]bool
		tracedSessions map[string]bool
		traces         int
	}
	counts := map[string]*repoCounts{}
	for _, pr := range prs {
		repo := string(pr.Repo)
		if counts[repo] == nil {
			counts[repo] = &repoCounts{tracedPRs: map[string]bool{}, tracedSessions: map[string]bool{}}
		}
		counts[repo].merged++
	}
	linkedSessions := map[string]bool{}
	for _, link := range traces {
		repo, ok := prOwner[link.PRID]
		if !ok {
			continue
		}
		counts[repo].tracedPRs[link.PRID] = true
		counts[repo].tracedSessions[link.SessionID] = true
		counts[repo].traces++
		linkedSessions[link.SessionID] = true
	}

	repos := make([]string, 0, len(counts))
	for repo := range counts {
		repos = append(repos, repo)
	}
	sort.Strings(repos)
	summary := Summary{
		TotalSessions:    len(sessions),
		SessionsWithKeys: withKeys,
		LinkedSessions:   len(linkedSessions),
		Traces:           len(traces),
	}
	for _, repo := range repos {
		c := counts[repo]
		summary.Repos = append(summary.Repos, RepoSummary{
			Repo:           repo,
			MergedPRs:      c.merged,
			TracedPRs:      len(c.tracedPRs),
			TracedSessions: len(c.tracedSessions),
			Traces:         c.traces,
		})
	}
	return summary
}

// SaveIndex writes the index as separate JSON documents under dir.
func SaveIndex(dir string, idx Index) error {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("create index directory %s: %w", dir, err)
	}
	files := []struct {
		name  string
		value any
	}{
		{"merged_prs.json", idx.MergedPRs},
		{"sessions.json", idx.Sessions},
		{"traces.json", idx.Traces},
		{"commits.json", idx.Commits},
		{"summary.json", idx.Summary},
	}
	for _, file := range files {
		if err := writeJSONFile(filepath.Join(dir, file.name), file.value); err != nil {
			return err
		}
	}
	return nil
}

// LoadIndex reads an index previously written by SaveIndex. Commit mappings
// are optional so indexes written before commit support still load.
func LoadIndex(dir string) (Index, error) {
	var idx Index
	if err := readJSONFile(filepath.Join(dir, "merged_prs.json"), &idx.MergedPRs); err != nil {
		return Index{}, err
	}
	if err := readJSONFile(filepath.Join(dir, "sessions.json"), &idx.Sessions); err != nil {
		return Index{}, err
	}
	if err := readJSONFile(filepath.Join(dir, "traces.json"), &idx.Traces); err != nil {
		return Index{}, err
	}
	if err := readJSONFile(filepath.Join(dir, "summary.json"), &idx.Summary); err != nil {
		return Index{}, err
	}
	commitsPath := filepath.Join(dir, "commits.json")
	if _, err := os.Stat(commitsPath); err == nil {
		if err := readJSONFile(commitsPath, &idx.Commits); err != nil {
			return Index{}, err
		}
	} else if !os.IsNotExist(err) {
		return Index{}, fmt.Errorf("stat %s: %w", commitsPath, err)
	}
	seen := map[string]bool{}
	for _, pr := range idx.MergedPRs {
		seen[string(pr.Repo)] = true
	}
	repos := make([]string, 0, len(seen))
	for repo := range seen {
		repos = append(repos, repo)
	}
	sort.Strings(repos)
	idx.Repos = repos
	return idx, nil
}

func writeJSONFile(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return fmt.Errorf("encode %s: %w", path, err)
	}
	data = append(data, '\n')
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return fmt.Errorf("write %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("replace %s: %w", path, err)
	}
	return nil
}

func readJSONFile(path string, value any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read %s: %w", path, err)
	}
	if err := json.Unmarshal(data, value); err != nil {
		return fmt.Errorf("decode %s: %w", path, err)
	}
	return nil
}
