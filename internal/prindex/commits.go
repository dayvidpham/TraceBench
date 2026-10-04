package prindex

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"sort"
	"strings"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

// CommitMapping records the pull requests one session commit hash maps to.
type CommitMapping struct {
	SessionID string   `json:"session_id"`
	Hash      string   `json:"hash"`
	PRs       []string `json:"prs,omitempty"`
}

// CommitResolution reports how session commits resolved to pull requests.
type CommitResolution struct {
	Mappings    []CommitMapping
	ByHash      map[string][]string
	TotalHashes int
	Resolved    int
	Unresolved  []string
}

// ResolveCommitPRs maps commit hashes to merged pull requests. A hash that
// matches a merge commit or head OID resolves locally; every other hash is
// looked up through the GitHub commits/{sha}/pulls endpoint, trying each
// repository. Cached mappings from a previous index are reused unless refresh
// is set; hashes that resolve to no pull request are cached as empty.
func ResolveCommitPRs(
	ctx context.Context,
	ghBin string,
	prs []corpus.PullRequest,
	sessionHashes map[string][]string,
	cached map[string][]string,
	refresh bool,
) (CommitResolution, error) {
	local := map[string][]string{}
	known := map[string]bool{}
	var repos []string
	seenRepo := map[string]bool{}
	for _, pr := range prs {
		known[pr.ID()] = true
		repo := string(pr.Repo)
		if !seenRepo[repo] {
			seenRepo[repo] = true
			repos = append(repos, repo)
		}
		for _, sha := range []string{pr.HeadOID, pr.MergeCommit} {
			if sha != "" {
				local[sha] = appendUnique(local[sha], pr.ID())
			}
		}
	}

	hashSet := map[string]bool{}
	for _, hashes := range sessionHashes {
		for _, hash := range hashes {
			hashSet[hash] = true
		}
	}
	hashes := make([]string, 0, len(hashSet))
	for hash := range hashSet {
		hashes = append(hashes, hash)
	}
	sort.Strings(hashes)

	resolution := CommitResolution{ByHash: map[string][]string{}, TotalHashes: len(hashes)}
	for _, hash := range hashes {
		if !refresh {
			if ids, ok := cached[hash]; ok {
				resolution.ByHash[hash] = ids
				if len(ids) > 0 {
					resolution.Resolved++
				}
				continue
			}
		}
		ids := local[hash]
		if len(ids) == 0 {
			for _, repo := range repos {
				found, err := lookupCommitPRs(ctx, ghBin, repo, hash, known)
				if err != nil {
					return resolution, err
				}
				if len(found) > 0 {
					ids = found
					break
				}
			}
		}
		resolution.ByHash[hash] = ids
		if len(ids) > 0 {
			resolution.Resolved++
		} else {
			resolution.Unresolved = append(resolution.Unresolved, hash)
		}
	}

	sessions := make([]string, 0, len(sessionHashes))
	for sessionID := range sessionHashes {
		sessions = append(sessions, sessionID)
	}
	sort.Strings(sessions)
	for _, sessionID := range sessions {
		for _, hash := range sessionHashes[sessionID] {
			resolution.Mappings = append(resolution.Mappings, CommitMapping{
				SessionID: sessionID,
				Hash:      hash,
				PRs:       resolution.ByHash[hash],
			})
		}
	}
	return resolution, nil
}

// CachedCommitPRs converts persisted mappings into a Hash -> PR IDs map.
func CachedCommitPRs(mappings []CommitMapping) map[string][]string {
	cached := make(map[string][]string, len(mappings))
	for _, mapping := range mappings {
		if _, ok := cached[mapping.Hash]; !ok {
			cached[mapping.Hash] = mapping.PRs
		}
	}
	return cached
}

type commitPull struct {
	Number   int     `json:"number"`
	MergedAt *string `json:"merged_at"`
}

func lookupCommitPRs(ctx context.Context, ghBin, repo, sha string, known map[string]bool) ([]string, error) {
	cmd := exec.CommandContext(ctx, ghBin, "api",
		fmt.Sprintf("repos/%s/commits/%s/pulls", repo, sha))
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		message := stderr.String()
		if strings.Contains(message, "HTTP 404") || strings.Contains(message, "HTTP 422") ||
			strings.Contains(message, "Not Found") || strings.Contains(message, "No commit found") {
			return nil, nil
		}
		return nil, fmt.Errorf("gh api commits/%s/pulls in %s: %w: %s", sha, repo, err, strings.TrimSpace(message))
	}
	var pulls []commitPull
	if err := json.Unmarshal(stdout.Bytes(), &pulls); err != nil {
		return nil, fmt.Errorf("decode pull requests for commit %s in %s: %w", sha, repo, err)
	}
	var ids []string
	for _, pull := range pulls {
		if pull.MergedAt == nil {
			continue
		}
		id := fmt.Sprintf("%s#%d", repo, pull.Number)
		if known[id] {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	return ids, nil
}

func appendUnique(values []string, value string) []string {
	for _, existing := range values {
		if existing == value {
			return values
		}
	}
	return append(values, value)
}
