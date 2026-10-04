package prindex

import (
	"strings"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

// DefaultIgnoredBranches are branch names that never identify pull request
// work: they are the repository's standing branches and worktree host branch.
var DefaultIgnoredBranches = []string{"main", "master", "develop", "dummy", "__dummy__", "HEAD"}

// KeyExtractor derives candidate worktree or branch identifiers for recorded
// sessions. A key is a git branch name, the first path component under the
// repository host checkout, or a Claude project directory name decoded from a
// session's source path. Keys are matched against pull request head refs.
type KeyExtractor struct {
	repoHost string
	ignored  map[string]bool
}

// NewKeyExtractor builds an extractor for sessions recorded under repoHost.
// ignoredBranches are branch names that never identify pull request work.
func NewKeyExtractor(repoHost string, ignoredBranches []string) *KeyExtractor {
	ignored := make(map[string]bool, len(ignoredBranches))
	for _, branch := range ignoredBranches {
		ignored[strings.ToLower(strings.TrimSpace(branch))] = true
	}
	return &KeyExtractor{
		repoHost: strings.TrimSuffix(repoHost, "/"),
		ignored:  ignored,
	}
}

// Keys returns the candidate keys for a session in deterministic order. A
// session with no direct keys inherits its parent's keys so that subagent
// sessions are attributed together with their owning session.
func (e *KeyExtractor) Keys(s corpus.Session, lookup map[string]corpus.Session) []string {
	seen := map[string]bool{}
	var keys []string
	add := func(candidates ...string) {
		for _, k := range candidates {
			lk := strings.ToLower(k)
			if k == "" || e.ignored[lk] || seen[k] {
				continue
			}
			seen[k] = true
			keys = append(keys, k)
		}
	}
	visited := map[string]bool{}
	var visit func(cur corpus.Session, depth int)
	visit = func(cur corpus.Session, depth int) {
		if depth > 8 || visited[cur.ID] {
			return
		}
		visited[cur.ID] = true
		direct := e.directKeys(cur)
		if len(direct) > 0 {
			add(direct...)
			return
		}
		if cur.ParentID != "" {
			if parent, ok := lookup[cur.ParentID]; ok {
				visit(parent, depth+1)
			}
		}
	}
	visit(s, 0)
	return keys
}

func (e *KeyExtractor) directKeys(s corpus.Session) []string {
	var keys []string
	for _, candidate := range []string{s.Worktree, s.SessionCwd, s.ProjectCwd} {
		addKey(&keys, e.pathKey(candidate))
	}
	addKey(&keys, strings.TrimSpace(s.Branch))
	for _, k := range e.sourcePathKeys(s.SourcePath) {
		addKey(&keys, k)
	}
	return keys
}

func addKey(keys *[]string, key string) {
	if key == "" {
		return
	}
	for _, existing := range *keys {
		if existing == key {
			return
		}
	}
	*keys = append(*keys, key)
}

// pathKey returns the first path component below the repository host, which
// is the worktree directory name for sessions recorded inside a worktree.
func (e *KeyExtractor) pathKey(p string) string {
	p = strings.TrimSuffix(strings.TrimSpace(p), "/")
	if p == "" || p == e.repoHost {
		return ""
	}
	prefix := e.repoHost + "/"
	if !strings.HasPrefix(p, prefix) {
		return ""
	}
	rest := strings.TrimPrefix(p, prefix)
	first, _, _ := strings.Cut(rest, "/")
	if first == "" || strings.HasPrefix(first, ".") {
		return ""
	}
	return first
}

// sourcePathKeys decodes the working directory encoded in a Claude Code
// project directory, such as
// "/home/u/.claude/projects/-home-u-repo-peasant-3--feat--x/session.jsonl".
func (e *KeyExtractor) sourcePathKeys(sourcePath string) []string {
	if e.repoHost == "" || sourcePath == "" {
		return nil
	}
	encodedHost := strings.ReplaceAll(e.repoHost, "/", "-")
	marker := "/.claude/projects/" + encodedHost + "-"
	idx := strings.Index(sourcePath, marker)
	if idx < 0 {
		return nil
	}
	rest := sourcePath[idx+len(marker):]
	name, _, ok := strings.Cut(rest, "/")
	if !ok || name == "" {
		return nil
	}
	// A nested worktree encodes its path with further dashes; keep the
	// component that belongs to the repository host.
	if i := strings.Index(name, "-worktree-"); i > 0 {
		name = name[:i]
	}
	return []string{name}
}
