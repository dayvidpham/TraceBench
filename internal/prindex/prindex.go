// Package prindex indexes merged pull requests and links them to recorded
// agent sessions. Links come from head-ref name matches, same-issue open
// windows, and commit associations; one session may link to several pull
// requests, for example when a worktree or branch is reused across an issue.
package prindex

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"sort"
	"strings"
	"time"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

// ghPullRequest mirrors the `gh pr list --json` fields used here. Body is
// the pull request description as published on GitHub; it stays raw for
// the reason documented on corpus.PullRequest.
type ghPullRequest struct {
	Number int    `json:"number"`
	Title  string `json:"title"`
	Body   string `json:"body"`
	URL    string `json:"url"`
	Author *struct {
		Login string `json:"login"`
	} `json:"author"`
	CreatedAt    time.Time `json:"createdAt"`
	MergedAt     time.Time `json:"mergedAt"`
	Additions    int       `json:"additions"`
	Deletions    int       `json:"deletions"`
	ChangedFiles int       `json:"changedFiles"`
	HeadRefName  string    `json:"headRefName"`
	HeadRefOID   string    `json:"headRefOid"`
	BaseRefName  string    `json:"baseRefName"`
	MergeCommit  *struct {
		OID string `json:"oid"`
	} `json:"mergeCommit"`
}

// ghIssue mirrors the `gh issue list --json` fields used here. Body is the
// issue description as published on GitHub; it stays raw for the reason
// documented on corpus.LinkedIssue.
type ghIssue struct {
	Number int    `json:"number"`
	Title  string `json:"title"`
	Body   string `json:"body"`
	URL    string `json:"url"`
}

// FetchMergedPRs lists every merged pull request of repo through the gh CLI.
func FetchMergedPRs(ctx context.Context, ghBin, repo string) ([]corpus.PullRequest, error) {
	const limit = 1000
	cmd := exec.CommandContext(ctx, ghBin,
		"pr", "list", "-R", repo,
		"--state", "merged",
		"--limit", fmt.Sprint(limit),
		"--json", strings.Join([]string{
			"number", "title", "body", "url", "author", "createdAt", "mergedAt",
			"additions", "deletions", "changedFiles", "headRefName",
			"headRefOid", "baseRefName", "mergeCommit",
		}, ","),
	)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("gh pr list %s: %w: %s", repo, err, strings.TrimSpace(stderr.String()))
	}

	var raw []ghPullRequest
	if err := json.Unmarshal(stdout.Bytes(), &raw); err != nil {
		return nil, fmt.Errorf("decode gh pr list output for %s: %w", repo, err)
	}
	if len(raw) == limit {
		return nil, fmt.Errorf("repo %s returned %d merged pull requests; result may be truncated", repo, limit)
	}

	prs := make([]corpus.PullRequest, 0, len(raw))
	for _, r := range raw {
		prs = append(prs, toPullRequest(repo, r))
	}
	sort.Slice(prs, func(i, j int) bool { return prs[i].Number < prs[j].Number })
	needsIssues := false
	for _, pr := range prs {
		if _, ok := corpus.IssueFromHeadRef(pr.HeadRef); ok {
			needsIssues = true
			break
		}
	}
	if needsIssues {
		issues, err := fetchIssues(ctx, ghBin, repo)
		if err != nil {
			return nil, err
		}
		if err := attachIssues(prs, issues); err != nil {
			return nil, err
		}
	}
	return prs, nil
}

// toPullRequest maps one `gh pr list --json` record to the corpus model.
// A missing body decodes to an empty Body, so indexes written before the
// body field still load.
func toPullRequest(repo string, r ghPullRequest) corpus.PullRequest {
	pr := corpus.PullRequest{
		Repo:         corpus.RepoSlug(repo),
		Number:       r.Number,
		Title:        r.Title,
		Body:         r.Body,
		URL:          r.URL,
		HeadRef:      r.HeadRefName,
		HeadOID:      r.HeadRefOID,
		BaseRef:      r.BaseRefName,
		CreatedAt:    r.CreatedAt.UTC(),
		MergedAt:     r.MergedAt.UTC(),
		Additions:    r.Additions,
		Deletions:    r.Deletions,
		ChangedFiles: r.ChangedFiles,
	}
	if r.Author != nil {
		pr.Author = r.Author.Login
	}
	if r.MergeCommit != nil {
		pr.MergeCommit = r.MergeCommit.OID
	}
	return pr
}

// fetchIssues lists every issue of repo through the gh CLI and maps it by
// number. It runs only when a merged pull request's head branch names an
// issue; the linked issue body is public repository metadata like the pull
// request body.
func fetchIssues(ctx context.Context, ghBin, repo string) (map[int]corpus.LinkedIssue, error) {
	const limit = 1000
	cmd := exec.CommandContext(ctx, ghBin,
		"issue", "list", "-R", repo,
		"--state", "all",
		"--limit", fmt.Sprint(limit),
		"--json", strings.Join([]string{"number", "title", "body", "url"}, ","),
	)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("gh issue list %s: %w: %s", repo, err, strings.TrimSpace(stderr.String()))
	}

	var raw []ghIssue
	if err := json.Unmarshal(stdout.Bytes(), &raw); err != nil {
		return nil, fmt.Errorf("decode gh issue list output for %s: %w", repo, err)
	}
	if len(raw) == limit {
		return nil, fmt.Errorf("repo %s returned %d issues; result may be truncated", repo, limit)
	}

	issues := make(map[int]corpus.LinkedIssue, len(raw))
	for _, r := range raw {
		issues[r.Number] = linkedIssue(r)
	}
	return issues, nil
}

// linkedIssue maps one `gh issue list --json` record to the corpus model.
func linkedIssue(r ghIssue) corpus.LinkedIssue {
	return corpus.LinkedIssue{Number: r.Number, Title: r.Title, Body: r.Body, URL: r.URL}
}

// attachIssues links every pull request whose head branch names an issue to
// the fetched issue record. A named issue the fetch did not return fails
// closed, so a truncated or inconsistent listing cannot silently drop the
// context.
func attachIssues(prs []corpus.PullRequest, issues map[int]corpus.LinkedIssue) error {
	for i := range prs {
		number, ok := corpus.IssueFromHeadRef(prs[i].HeadRef)
		if !ok {
			continue
		}
		issue, ok := issues[number]
		if !ok {
			return fmt.Errorf(
				"pull request %s names issue %d in head ref %q, but the issue fetch did not return it",
				prs[i].ID(), number, prs[i].HeadRef,
			)
		}
		link := issue
		prs[i].Issue = &link
	}
	return nil
}

// LinkInput carries the evidence used to link sessions to pull requests.
type LinkInput struct {
	// SessionCommits maps a session id to the commit hashes observed in it.
	SessionCommits map[string][]string
	// CommitPRs maps a commit hash to the pull request ids it belongs to.
	CommitPRs map[string][]string
}

// Attribute links sessions to pull requests. A session links to:
//   - every pull request whose head ref matches one of its keys (exact);
//   - every same-issue pull request that was open while the session ran
//     (issue_window), plus the first same-issue merge after it ended
//     (next_merge);
//   - the latest same-issue merge already merged when nothing else matches
//     (latest_merge);
//   - every pull request associated with its commits (commit).
//
// A session may produce several links; methods are recorded per link.
func Attribute(sessions []corpus.Session, prs []corpus.PullRequest, extractor *KeyExtractor, input LinkInput) []corpus.TraceLink {
	byRef := map[string][]int{}
	byIssue := map[int][]int{}
	byID := make(map[string]corpus.Session, len(sessions))
	prIndexByID := make(map[string]int, len(prs))
	for i, pr := range prs {
		byRef[pr.HeadRef] = append(byRef[pr.HeadRef], i)
		if issue, ok := corpus.IssueFromHeadRef(pr.HeadRef); ok {
			byIssue[issue] = append(byIssue[issue], i)
		}
		prIndexByID[pr.ID()] = i
	}
	for _, s := range sessions {
		byID[s.ID] = s
	}
	for _, indices := range byIssue {
		sort.Slice(indices, func(a, b int) bool { return earlierPR(prs[indices[a]], prs[indices[b]]) })
	}

	var links []corpus.TraceLink
	for _, s := range sessions {
		keys := extractor.Keys(s, byID)
		commits := input.SessionCommits[s.ID]
		if len(keys) == 0 && len(commits) == 0 {
			continue
		}

		candidates := map[int]bool{}
		exact := map[int]bool{}
		for _, key := range keys {
			for _, i := range byRef[key] {
				candidates[i] = true
				exact[i] = true
			}
			if issue, ok := corpus.IssueFromHeadRef(key); ok {
				for _, i := range byIssue[issue] {
					candidates[i] = true
				}
			}
		}

		start := time.UnixMilli(s.StartMS).UTC()
		end := s.End()
		linked := map[int]corpus.Attribution{}
		addLink := func(i int, method corpus.Attribution) {
			if existing, ok := linked[i]; !ok || methodPriority(method) > methodPriority(existing) {
				linked[i] = method
			}
		}

		for i := range exact {
			addLink(i, corpus.AttributionExact)
		}
		nextMerge := -1
		for i := range candidates {
			pr := prs[i]
			if !pr.CreatedAt.After(end) && !pr.MergedAt.Before(start) {
				addLink(i, corpus.AttributionIssueWindow)
			}
			if !pr.MergedAt.Before(end) && (nextMerge == -1 || earlierPR(pr, prs[nextMerge])) {
				nextMerge = i
			}
		}
		if nextMerge != -1 {
			addLink(nextMerge, corpus.AttributionNextMerge)
		}
		if len(linked) == 0 {
			latest := -1
			for i := range candidates {
				if prs[i].MergedAt.Before(start) && (latest == -1 || laterPR(prs[i], prs[latest])) {
					latest = i
				}
			}
			if latest != -1 {
				addLink(latest, corpus.AttributionLatestMerge)
			}
		}
		for _, hash := range commits {
			for _, id := range input.CommitPRs[hash] {
				if i, ok := prIndexByID[id]; ok {
					addLink(i, corpus.AttributionCommit)
				}
			}
		}

		for i, method := range linked {
			links = append(links, corpus.TraceLink{
				PRID:      prs[i].ID(),
				SessionID: s.ID,
				Method:    method,
				Keys:      keys,
			})
		}
	}
	sort.Slice(links, func(i, j int) bool {
		if links[i].PRID != links[j].PRID {
			return links[i].PRID < links[j].PRID
		}
		if links[i].SessionID != links[j].SessionID {
			return links[i].SessionID < links[j].SessionID
		}
		return methodPriority(links[i].Method) > methodPriority(links[j].Method)
	})
	return links
}

func methodPriority(m corpus.Attribution) int {
	switch m {
	case corpus.AttributionExact:
		return 4
	case corpus.AttributionCommit:
		return 3
	case corpus.AttributionIssueWindow:
		return 2
	case corpus.AttributionNextMerge:
		return 1
	default:
		return 0
	}
}

func earlierPR(a, b corpus.PullRequest) bool {
	if !a.MergedAt.Equal(b.MergedAt) {
		return a.MergedAt.Before(b.MergedAt)
	}
	return a.ID() < b.ID()
}

func laterPR(a, b corpus.PullRequest) bool {
	if !a.MergedAt.Equal(b.MergedAt) {
		return a.MergedAt.After(b.MergedAt)
	}
	return a.ID() > b.ID()
}
