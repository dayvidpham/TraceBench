// Package corpus defines the shared data model for the TraceBench pull
// request corpus: merged pull requests, recorded agent sessions, and the
// trace links that pair them.
package corpus

import (
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// RepoSlug identifies a GitHub repository as "owner/name".
type RepoSlug string

// DirName returns a filesystem-safe form of the slug.
func (r RepoSlug) DirName() string {
	return strings.ReplaceAll(string(r), "/", "--")
}

// PullRequest is one merged GitHub pull request. Body carries the pull
// request description as published on GitHub; it stays raw (no redaction
// pipeline) because it is already-public repository metadata, like Title
// and URL. This tool indexes public repositories only. The redact engine
// covers private session transcripts and metadata only. Older index and
// dump records without a body decode to an empty Body. Body uses
// `json:"body,omitempty"`, so an empty Body encodes without the body key:
// empty and absent are indistinguishable on the wire, and no reader
// distinguishes them.
type PullRequest struct {
	Repo         RepoSlug     `json:"repo"`
	Number       int          `json:"number"`
	Title        string       `json:"title"`
	Body         string       `json:"body,omitempty"`
	URL          string       `json:"url"`
	Author       string       `json:"author,omitempty"`
	HeadRef      string       `json:"head_ref"`
	HeadOID      string       `json:"head_oid,omitempty"`
	BaseRef      string       `json:"base_ref,omitempty"`
	MergeCommit  string       `json:"merge_commit,omitempty"`
	Issue        *LinkedIssue `json:"issue,omitempty"`
	CreatedAt    time.Time    `json:"created_at"`
	MergedAt     time.Time    `json:"merged_at"`
	Additions    int          `json:"additions"`
	Deletions    int          `json:"deletions"`
	ChangedFiles int          `json:"changed_files"`
}

// LinkedIssue is the GitHub issue named by a pull request's head branch, for
// example "peasant-337--..." names issue 337. Body is the already-public
// issue description and stays raw for the same reason as PullRequest.Body.
// A pull request whose branch names no issue, and records written before the
// field existed, decode to a nil Issue.
type LinkedIssue struct {
	Number int    `json:"number"`
	Title  string `json:"title,omitempty"`
	Body   string `json:"body,omitempty"`
	URL    string `json:"url,omitempty"`
}

// ID returns the stable "owner/name#number" identifier.
func (p PullRequest) ID() string {
	return fmt.Sprintf("%s#%d", p.Repo, p.Number)
}

// LinesChanged is the review size signal used for size stratification.
func (p PullRequest) LinesChanged() int {
	return p.Additions + p.Deletions
}

// Session is one recorded agent session from the Peasant database.
type Session struct {
	ID            string `json:"id"`
	ParentID      string `json:"parent_id,omitempty"`
	Harness       string `json:"harness"`
	ModelID       string `json:"model_id"`
	StartMS       int64  `json:"start_ms"`
	EndMS         int64  `json:"end_ms"`
	Branch        string `json:"branch,omitempty"`
	Worktree      string `json:"worktree,omitempty"`
	SessionCwd    string `json:"session_cwd,omitempty"`
	ProjectCwd    string `json:"project_cwd,omitempty"`
	ProjectRemote string `json:"project_remote,omitempty"`
	SourcePath    string `json:"source_path,omitempty"`
}

// End returns the session end time.
func (s Session) End() time.Time {
	return time.UnixMilli(s.EndMS).UTC()
}

// Attribution describes how a session was linked to a pull request.
type Attribution string

const (
	// AttributionExact marks a session whose worktree or branch names the
	// pull request head ref directly.
	AttributionExact Attribution = "exact"
	// AttributionCommit marks a session linked through a commit associated
	// with the pull request.
	AttributionCommit Attribution = "commit"
	// AttributionIssueWindow marks a session attributed through the issue
	// number encoded in its keys and the pull request's open window.
	AttributionIssueWindow Attribution = "issue_window"
	// AttributionNextMerge marks a session attributed to the first same-issue
	// pull request merged after the session ended.
	AttributionNextMerge Attribution = "next_merge"
	// AttributionLatestMerge marks a session with no merge after it; it is
	// attributed to the latest same-issue pull request already merged.
	AttributionLatestMerge Attribution = "latest_merge"
	// AttributionContext marks a session copied into a pull request bundle
	// because it is a parent, child, or fork of an attributed session.
	AttributionContext Attribution = "context"
)

// TraceLink pairs one session with the pull request it is attributed to.
type TraceLink struct {
	PRID      string      `json:"pr"`
	SessionID string      `json:"session_id"`
	Method    Attribution `json:"method"`
	Keys      []string    `json:"keys,omitempty"`
}

// SessionEdge is a lineage edge from one session to another, such as a
// session that was started by or continued from another session.
type SessionEdge struct {
	From string `json:"from"`
	To   string `json:"to"`
	Kind string `json:"kind"`
}

// Transcript source kinds recorded in the dataset manifest.
const (
	// TranscriptRawFile marks a byte-for-byte copy of a file-backed session.
	TranscriptRawFile = "raw_file"
	// TranscriptExport marks a per-entry export built from the Peasant
	// full-content capture when the raw source is not a plain file.
	TranscriptExport = "db_export"
)

// OpenedTranscript is one readable transcript with provenance.
type OpenedTranscript struct {
	Reader   io.ReadCloser
	Ext      string
	Source   string
	Complete bool
	Detail   string
}

var issuePattern = regexp.MustCompile(`^(?:peasant-)?([0-9]+)--`)

// IssueFromHeadRef extracts the issue number encoded in a branch name such as
// "peasant-343--feat--corpus-sampler" or "113--release-guard-dedup". The
// second return value reports whether an issue number was found.
func IssueFromHeadRef(ref string) (int, bool) {
	m := issuePattern.FindStringSubmatch(strings.TrimSpace(ref))
	if m == nil {
		return 0, false
	}
	n, err := strconv.Atoi(m[1])
	if err != nil {
		return 0, false
	}
	return n, true
}
