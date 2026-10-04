package snapshot

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// FileEntry is one blob in the repo tree at the cutoff SHA.
// Working-tree equivalence is enforced by reading blobs (not the FS),
// mirroring the tree-vs-blob check in scripts/verify-issue-1.sh.
type FileEntry struct {
	Path    string `json:"path"`
	BlobSHA string `json:"blob_sha"`
	Mode    string `json:"mode"`
	Size    int64  `json:"size"`
}

// Commit is one git commit at or before the cutoff.
type Commit struct {
	SHA           string    `json:"sha"`
	Parents       []string  `json:"parents"`
	AuthorTime    time.Time `json:"author_time"`
	CommitterTime time.Time `json:"committer_time"`
	Subject       string    `json:"subject"`
}

func gitRun(repo string, timeout time.Duration, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	full := append([]string{"-C", repo}, args...)
	cmd := exec.CommandContext(ctx, "git", full...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		if ctx.Err() == context.DeadlineExceeded {
			return "", fmt.Errorf("snapshot: git timed out: %s", strings.Join(args, " "))
		}
		return "", fmt.Errorf("snapshot: git %s failed: %s",
			strings.Join(args, " "), truncate(stderr.String(), 2000))
	}
	return stdout.String(), nil
}

func formatGitTime(t time.Time) string {
	return t.UTC().Format("2006-01-02T15:04:05+00:00")
}

// ResolveSHABefore returns the newest commit at or before cutoff.
func ResolveSHABefore(repo string, cutoff time.Time) (string, error) {
	out, err := gitRun(repo, 60*time.Second, "rev-list", "-1",
		"--before="+formatGitTime(cutoff), "HEAD")
	if err != nil {
		return "", err
	}
	sha := strings.TrimSpace(out)
	if sha == "" {
		return "", fmt.Errorf("snapshot: no commit at or before cutoff %s",
			cutoff.UTC().Format(time.RFC3339))
	}
	return sha, nil
}

// ListTree returns the deterministic (path-sorted) blob list at sha.
func ListTree(repo, sha string) ([]FileEntry, error) {
	out, err := gitRun(repo, 60*time.Second, "ls-tree", "-r", "--full-tree", sha)
	if err != nil {
		return nil, err
	}
	var entries []FileEntry
	for _, line := range strings.Split(out, "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		// "<mode> <type> <sha>\t<path>"
		tab := strings.Index(line, "\t")
		if tab < 0 {
			continue
		}
		meta, path := line[:tab], line[tab+1:]
		parts := strings.Fields(meta)
		if len(parts) != 3 {
			continue
		}
		mode, objType, objSHA := parts[0], parts[1], parts[2]
		if objType != "blob" {
			continue
		}
		sizeOut, err := gitRun(repo, 60*time.Second, "cat-file", "-s", objSHA)
		if err != nil {
			return nil, err
		}
		size, err := strconv.ParseInt(strings.TrimSpace(sizeOut), 10, 64)
		if err != nil {
			return nil, fmt.Errorf("snapshot: bad blob size for %s: %v", objSHA, err)
		}
		entries = append(entries, FileEntry{Path: path, BlobSHA: objSHA, Mode: mode, Size: size})
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Path < entries[j].Path })
	return entries, nil
}

// ListHistory returns commits with committer date <= cutoff,
// oldest-first with SHA tie-break for determinism.
func ListHistory(repo string, cutoff time.Time) ([]Commit, error) {
	format := "%H%x00%P%x00%aI%x00%cI%x00%s%x1f"
	out, err := gitRun(repo, 60*time.Second, "log",
		"--before="+formatGitTime(cutoff), "--format="+format)
	if err != nil {
		return nil, err
	}
	var commits []Commit
	for _, rec := range strings.Split(out, "\x1f") {
		rec = strings.Trim(rec, "\n")
		if strings.TrimSpace(rec) == "" {
			continue
		}
		fields := strings.SplitN(rec, "\x00", 5)
		if len(fields) != 5 {
			continue
		}
		author, err := ParseTime(fields[2])
		if err != nil {
			return nil, fmt.Errorf("snapshot: bad author time %q: %v", fields[2], err)
		}
		committer, err := ParseTime(fields[3])
		if err != nil {
			return nil, fmt.Errorf("snapshot: bad committer time %q: %v", fields[3], err)
		}
		var parents []string
		if strings.TrimSpace(fields[1]) != "" {
			parents = strings.Fields(fields[1])
		}
		commits = append(commits, Commit{
			SHA: fields[0], Parents: parents,
			AuthorTime: author, CommitterTime: committer, Subject: fields[4],
		})
	}
	sort.Slice(commits, func(i, j int) bool {
		if commits[i].CommitterTime.Equal(commits[j].CommitterTime) {
			return commits[i].SHA < commits[j].SHA
		}
		return commits[i].CommitterTime.Before(commits[j].CommitterTime)
	})
	return commits, nil
}

// MaterializeTree writes the exact tree at sha into dest via git archive.
func MaterializeTree(repo, sha, dest string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	archive := exec.CommandContext(ctx, "git", "-C", repo, "archive", sha)
	var buf bytes.Buffer
	var errBuf bytes.Buffer
	archive.Stdout = &buf
	archive.Stderr = &errBuf
	if err := archive.Run(); err != nil {
		return fmt.Errorf("snapshot: git archive %s failed: %.1000q", sha, errBuf.String())
	}
	extract := exec.CommandContext(ctx, "tar", "-x", "-C", dest)
	extract.Stdin = &buf
	var xErr bytes.Buffer
	extract.Stderr = &xErr
	if err := extract.Run(); err != nil {
		return fmt.Errorf("snapshot: tar extract failed: %.1000q", xErr.String())
	}
	_ = filepath.Clean(dest)
	return nil
}

// ResolveCommit verifies sha names a commit and returns its full SHA, tree SHA,
// and committer time. It fails closed, naming the commit, when it is absent.
func ResolveCommit(repo, sha string) (full, tree string, committed time.Time, err error) {
	out, err := gitRun(repo, 60*time.Second, "rev-parse", "--verify", "--quiet", sha+"^{commit}")
	if err != nil || strings.TrimSpace(out) == "" {
		return "", "", time.Time{}, fmt.Errorf(
			"snapshot: commit %q not found in %s; fetch it (git fetch origin %s) or fix the commit cutoff",
			sha, repo, sha)
	}
	full = strings.TrimSpace(out)
	if tree, err = TreeSHA(repo, full); err != nil {
		return "", "", time.Time{}, err
	}
	when, err := gitRun(repo, 60*time.Second, "show", "-s", "--format=%cI", full)
	if err != nil {
		return "", "", time.Time{}, err
	}
	committed, err = ParseTime(strings.TrimSpace(when))
	if err != nil {
		return "", "", time.Time{}, fmt.Errorf("snapshot: bad committer date for %s: %v", full, err)
	}
	return full, tree, committed, nil
}

// TreeSHA returns git rev-parse <sha>^{tree}.
func TreeSHA(repo, sha string) (string, error) {
	out, err := gitRun(repo, 60*time.Second, "rev-parse", "--verify", sha+"^{tree}")
	if err != nil {
		return "", fmt.Errorf("snapshot: cannot resolve tree of %s: %v", sha, err)
	}
	return strings.TrimSpace(out), nil
}
