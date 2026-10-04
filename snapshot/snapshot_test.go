package snapshot

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func git(t *testing.T, repo string, env []string, args ...string) {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", repo}, args...)...)
	cmd.Env = append(os.Environ(), env...)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
}

func commitAt(t *testing.T, repo, name, content, iso string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(repo, name), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	git(t, repo, nil, "add", name)
	env := []string{
		"GIT_AUTHOR_DATE=" + iso, "GIT_COMMITTER_DATE=" + iso,
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@t",
		"GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@t",
	}
	git(t, repo, env, "-c", "user.name=t", "-c", "user.email=t@t",
		"commit", "-m", name)
}

func testRepo(t *testing.T) string {
	t.Helper()
	repo := filepath.Join(t.TempDir(), "repo")
	if err := os.MkdirAll(repo, 0o755); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("git", "init", repo)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git init: %v\n%s", err, out)
	}
	commitAt(t, repo, "a.txt", "v1", "2026-01-01T00:00:00+00:00")
	commitAt(t, repo, "b.txt", "v2", "2026-02-01T00:00:00+00:00")
	commitAt(t, repo, "c.txt", "v3", "2026-03-01T00:00:00+00:00")
	return repo
}

func traceDir(t *testing.T) string {
	t.Helper()
	d := filepath.Join(t.TempDir(), "traces")
	if err := os.MkdirAll(d, 0o755); err != nil {
		t.Fatal(err)
	}
	early := filepath.Join(d, "early.jsonl")
	late := filepath.Join(d, "late.jsonl")
	os.WriteFile(early, []byte("{}\n"), 0o644)
	os.WriteFile(late, []byte("{}\n"), 0o644)
	tEarly := time.Date(2026, 1, 15, 0, 0, 0, 0, time.UTC)
	tLate := time.Date(2026, 3, 15, 0, 0, 0, 0, time.UTC)
	os.Chtimes(early, tEarly, tEarly)
	os.Chtimes(late, tLate, tLate)
	return d
}

func subjects(cs []Commit) []string {
	var out []string
	for _, c := range cs {
		out = append(out, c.Subject)
	}
	return out
}

func filePaths(fs []FileEntry) []string {
	var out []string
	for _, f := range fs {
		out = append(out, f.Path)
	}
	return out
}

func tracePaths(ts []TraceFile) []string {
	var out []string
	for _, x := range ts {
		out = append(out, x.Path)
	}
	return out
}

func equalStr(t *testing.T, got, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for i := range got {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}

func mustDate(t *testing.T, iso string) Cutoff {
	t.Helper()
	c, err := ByDate(iso)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestDateCutoffExcludesLater(t *testing.T) {
	repo := testRepo(t)
	s, err := SnapshotRepo(repo, mustDate(t, "2026-02-01T00:00:00Z"),
		Options{TraceDir: traceDir(t)})
	if err != nil {
		t.Fatal(err)
	}
	equalStr(t, subjects(s.Commits), []string{"a.txt", "b.txt"})
	equalStr(t, filePaths(s.Files), []string{"a.txt", "b.txt"})
	equalStr(t, tracePaths(s.Traces), []string{"early.jsonl"})
	for _, c := range s.Commits {
		if c.CommitterTime.After(s.CutoffTime) {
			t.Fatalf("commit after cutoff: %s", c.SHA)
		}
	}
}

func TestPRCutoffIsExclusive(t *testing.T) {
	repo := testRepo(t)
	stub := StubPeasantClient{Starts: map[int]time.Time{
		7: time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC),
	}}
	c, err := ByPR(7)
	if err != nil {
		t.Fatal(err)
	}
	s, err := SnapshotRepo(repo, c, Options{Peasant: stub})
	if err != nil {
		t.Fatal(err)
	}
	equalStr(t, subjects(s.Commits), []string{"a.txt"})
	equalStr(t, filePaths(s.Files), []string{"a.txt"})
}

func TestPRStartOverrideBypassesBinary(t *testing.T) {
	repo := testRepo(t)
	c, err := ByPR(99)
	if err != nil {
		t.Fatal(err)
	}
	s, err := SnapshotRepo(repo, c,
		Options{PRStartOverride: "2026-02-01T00:00:00Z"})
	if err != nil {
		t.Fatal(err)
	}
	equalStr(t, subjects(s.Commits), []string{"a.txt"})
}

func TestDeterminism(t *testing.T) {
	repo := testRepo(t)
	dir := traceDir(t)
	a, err := SnapshotRepo(repo, mustDate(t, "2026-03-01T00:00:00Z"),
		Options{TraceDir: dir})
	if err != nil {
		t.Fatal(err)
	}
	b, err := SnapshotRepo(repo, mustDate(t, "2026-03-01T00:00:00Z"),
		Options{TraceDir: dir})
	if err != nil {
		t.Fatal(err)
	}
	ha, err := a.ManifestHash()
	if err != nil {
		t.Fatal(err)
	}
	hb, err := b.ManifestHash()
	if err != nil {
		t.Fatal(err)
	}
	if ha != hb {
		t.Fatalf("manifest mismatch %s != %s", ha, hb)
	}
	ra, _ := json.Marshal(snapshotJSON(*a))
	rb, _ := json.Marshal(snapshotJSON(*b))
	if string(ra) != string(rb) {
		t.Fatal("snapshots differ")
	}
	if a.TreeSHA == "" || a.TreeSHA != revParse(t, repo, a.RepoSHA+"^{tree}") {
		t.Fatalf("tree_sha %q does not match %s^{tree}", a.TreeSHA, a.RepoSHA)
	}
}

func revParse(t *testing.T, repo, rev string) string {
	t.Helper()
	out, err := exec.Command("git", "-C", repo, "rev-parse", rev).Output()
	if err != nil {
		t.Fatalf("rev-parse %s: %v", rev, err)
	}
	return strings.TrimSpace(string(out))
}

func TestCommitCutoffPinsExactTree(t *testing.T) {
	repo := testRepo(t)
	// Pin a side-branch commit unreachable from HEAD. Time selection at its
	// committer date would pick b.txt's commit from HEAD; the exact tree wins.
	git(t, repo, nil, "checkout", "-q", "-b", "side", "HEAD~1")
	commitAt(t, repo, "e.txt", "side", "2026-02-15T00:00:00+00:00")
	pinned := revParse(t, repo, "HEAD")
	git(t, repo, nil, "checkout", "-q", "-")
	c, err := ByCommit(pinned)
	if err != nil {
		t.Fatal(err)
	}
	s, err := SnapshotRepo(repo, c, Options{})
	if err != nil {
		t.Fatal(err)
	}
	if s.RepoSHA != pinned {
		t.Fatalf("repo_sha %s, want pinned %s", s.RepoSHA, pinned)
	}
	if s.TreeSHA != revParse(t, repo, pinned+"^{tree}") {
		t.Fatalf("tree_sha %s mismatch", s.TreeSHA)
	}
	equalStr(t, filePaths(s.Files), []string{"a.txt", "b.txt", "e.txt"})
	if !s.CutoffTime.Equal(time.Date(2026, 2, 15, 0, 0, 0, 0, time.UTC)) {
		t.Fatalf("cutoff_time %s, want the commit's committer date", s.CutoffTime)
	}
	if s.CutoffKind != CutoffCommit {
		t.Fatalf("cutoff_kind %q", s.CutoffKind)
	}
}

func TestCommitCutoffMissingCommitFailsClosed(t *testing.T) {
	repo := testRepo(t)
	bogus := "0123456789abcdef0123456789abcdef01234567"
	c, err := ByCommit(bogus)
	if err != nil {
		t.Fatal(err)
	}
	_, err = SnapshotRepo(repo, c, Options{})
	if err == nil || !strings.Contains(err.Error(), bogus) {
		t.Fatalf("want error naming %s, got %v", bogus, err)
	}
	if _, err := ByCommit(" "); err == nil {
		t.Fatal("empty commit must be rejected")
	}
}

func TestCLICommitMaterialize(t *testing.T) {
	repo := testRepo(t)
	pinned := revParse(t, repo, "HEAD~1")
	out := filepath.Join(t.TempDir(), "out")
	cmd := exec.Command("go", "run", "./cmd/snapshot",
		"--repo", repo, "--cutoff-type", "commit", "--commit", pinned,
		"--out", out, "--materialize")
	if b, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("cli: %v\n%s", err, b)
	}
	raw, err := os.ReadFile(filepath.Join(out, "history.json"))
	if err != nil {
		t.Fatal(err)
	}
	var payload struct {
		RepoSHA string `json:"repo_sha"`
		TreeSHA string `json:"tree_sha"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatal(err)
	}
	if payload.RepoSHA != pinned || payload.TreeSHA != revParse(t, repo, pinned+"^{tree}") {
		t.Fatalf("unexpected payload: %s", raw)
	}
	if _, err := os.Stat(filepath.Join(out, "repo", "b.txt")); err != nil {
		t.Fatalf("materialized tree missing b.txt: %v", err)
	}
	if _, err := os.Stat(filepath.Join(out, "repo", "c.txt")); err == nil {
		t.Fatal("materialized tree contains c.txt from after the pinned commit")
	}
}

func writeFakePeasant(t *testing.T, script string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "peasant")
	if err := os.WriteFile(p, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestBinaryClientParsesJSON(t *testing.T) {
	bin := writeFakePeasant(t, "#!/bin/sh\n"+
		"if [ \"$1\" = \"--version\" ]; then echo \"peasant 1.2.3\"; exit 0; fi\n"+
		"echo '{\"started_at\": \"2026-02-01T00:00:00Z\"}'\n")
	c := BinaryPeasantClient{Binary: bin, ExpectedVersion: "1.2.3"}
	v, err := c.Version()
	if err != nil {
		t.Fatal(err)
	}
	if v != "peasant 1.2.3" {
		t.Fatalf("version = %q", v)
	}
	start, err := c.PRStart(7)
	if err != nil {
		t.Fatal(err)
	}
	want := time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC)
	if !start.Equal(want) {
		t.Fatalf("start = %s, want %s", start, want)
	}
}

func TestBinaryClientVersionMismatch(t *testing.T) {
	bin := writeFakePeasant(t, "#!/bin/sh\necho \"peasant 9.9.9\"\n")
	c := BinaryPeasantClient{Binary: bin, ExpectedVersion: "1.2.3"}
	if _, err := c.Version(); err == nil {
		t.Fatal("expected version mismatch error")
	}
}

func TestDirProviderMissingRoot(t *testing.T) {
	p := DirTraceProvider{Root: filepath.Join(t.TempDir(), "nope")}
	files, err := p.ListFiles(time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if len(files) != 0 {
		t.Fatalf("expected empty, got %v", files)
	}
}

func TestCLIEndToEnd(t *testing.T) {
	repo := testRepo(t)
	out := filepath.Join(t.TempDir(), "out")
	cmd := exec.Command("go", "run", "./cmd/snapshot",
		"--repo", repo, "--cutoff-type", "date",
		"--cutoff-date", "2026-02-01T00:00:00Z", "--out", out)
	cmd.Dir = "."
	if b, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("cli: %v\n%s", err, b)
	}
	raw, err := os.ReadFile(filepath.Join(out, "history.json"))
	if err != nil {
		t.Fatal(err)
	}
	var payload struct {
		Commits []struct {
			Subject string `json:"subject"`
		} `json:"commits"`
		Manifest string `json:"manifest_sha256"`
		TreeSHA  string `json:"tree_sha"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Commits) != 2 || payload.Manifest == "" || payload.TreeSHA == "" {
		t.Fatalf("unexpected payload: %s", raw)
	}
}
