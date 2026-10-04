package collector

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/dayvidpham/TraceBench/internal/corpus"
	"github.com/dayvidpham/TraceBench/internal/sampler"
)

func TestCollectCopiesTranscriptsAndRecordsMissing(t *testing.T) {
	workDir := t.TempDir()
	sourceDir := filepath.Join(workDir, "sources")
	if err := os.MkdirAll(sourceDir, 0o755); err != nil {
		t.Fatal(err)
	}
	content := []byte(`{"type":"user","text":"hello"}` + "\n")
	sourcePath := filepath.Join(sourceDir, "session-one.jsonl")
	if err := os.WriteFile(sourcePath, content, 0o644); err != nil {
		t.Fatal(err)
	}

	pr := corpus.PullRequest{
		Repo: corpus.RepoSlug("peasant-labs/peasant"), Number: 343,
		Title: "feat: thing", Body: "Intent text for the change.",
		HeadRef:  "peasant-343--feat--thing",
		MergedAt: time.Date(2026, 9, 13, 0, 0, 0, 0, time.UTC),
	}
	bundles := []Bundle{
		{
			Assignment: sampler.Assignment{PR: pr, Split: sampler.Train, Group: "peasant-labs/peasant#issue-343"},
			Sessions: []SessionTrace{
				{Session: corpus.Session{
					ID: "session-one", SourcePath: sourcePath, StartMS: 1000, EndMS: 2000,
					Worktree: "/home/someone/repo/peasant-343--feat--thing",
				}, Method: corpus.AttributionExact},
				{Session: corpus.Session{ID: "session-missing", SourcePath: filepath.Join(sourceDir, "gone.jsonl")}, Method: corpus.AttributionNextMerge},
			},
		},
	}

	datasetDir := filepath.Join(workDir, "dataset")
	manifest, err := Collect(context.Background(), datasetDir, bundles, Options{
		AllowMissing: true,
		Targets:      map[string]int{"train": 1},
		Database:     "/tmp/peasant.db",
		Seed:         42,
	})
	if err != nil {
		t.Fatalf("Collect: %v", err)
	}
	if manifest.SchemaVersion != ManifestSchemaVersion {
		t.Fatalf("schema version %d, want %d", manifest.SchemaVersion, ManifestSchemaVersion)
	}
	if manifest.Counts["train"] != 1 || manifest.Targets["train"] != 1 {
		t.Fatalf("counts %v, targets %v", manifest.Counts, manifest.Targets)
	}
	if len(manifest.Missing) != 1 || manifest.Missing[0] != "session-missing" {
		t.Fatalf("missing list %v", manifest.Missing)
	}
	if len(manifest.PRs) != 1 {
		t.Fatalf("pull request records %d, want 1", len(manifest.PRs))
	}
	record := manifest.PRs[0]
	if record.ID != "peasant-labs/peasant#343" || record.Sessions != 2 {
		t.Fatalf("record %+v", record)
	}
	if record.Body != "Intent text for the change." {
		t.Fatalf("record body %q", record.Body)
	}

	var copied Transcript
	var missing Transcript
	for _, transcript := range record.Transcripts {
		switch transcript.SessionID {
		case "session-one":
			copied = transcript
		case "session-missing":
			missing = transcript
		}
	}
	if copied.Path == "" || copied.Missing {
		t.Fatalf("copied transcript %+v", copied)
	}
	copiedBytes, err := os.ReadFile(filepath.Join(datasetDir, copied.Path))
	if err != nil {
		t.Fatalf("read copied transcript: %v", err)
	}
	if string(copiedBytes) != string(content) {
		t.Fatalf("copied bytes %q, want %q", copiedBytes, content)
	}
	sum := sha256.Sum256(content)
	if copied.SHA256 != hex.EncodeToString(sum[:]) {
		t.Fatalf("sha256 %s, want %s", copied.SHA256, hex.EncodeToString(sum[:]))
	}
	if copied.Size != int64(len(content)) {
		t.Fatalf("size %d, want %d", copied.Size, len(content))
	}
	if !missing.Missing || missing.Error == "" {
		t.Fatalf("missing transcript %+v", missing)
	}

	if _, err := os.Stat(filepath.Join(datasetDir, "manifest.json")); err != nil {
		t.Fatalf("manifest.json: %v", err)
	}
	prFile := filepath.Join(datasetDir, "train", "peasant-labs--peasant", "pr-0343", "pr.json")
	prBytes, err := os.ReadFile(prFile)
	if err != nil {
		t.Fatalf("pr.json: %v", err)
	}
	if strings.Contains(string(prBytes), "/home/") || strings.Contains(string(prBytes), sourceDir) {
		t.Fatalf("pr.json leaks local paths:\n%s", prBytes)
	}
	if !strings.Contains(string(prBytes), "peasant-343--feat--thing") {
		t.Fatalf("pr.json lost the portable worktree name:\n%s", prBytes)
	}
}

func TestCollectFailsOnMissingTranscript(t *testing.T) {
	workDir := t.TempDir()
	pr := corpus.PullRequest{Repo: corpus.RepoSlug("peasant-labs/peasant"), Number: 7, HeadRef: "x"}
	bundles := []Bundle{{
		Assignment: sampler.Assignment{PR: pr, Split: sampler.Test},
		Sessions: []SessionTrace{{
			Session: corpus.Session{ID: "gone", SourcePath: filepath.Join(workDir, "nope.json")},
		}},
	}}
	manifest, err := Collect(context.Background(), filepath.Join(workDir, "dataset"), bundles, Options{})
	if err == nil {
		t.Fatal("Collect succeeded despite a missing transcript")
	}
	if manifest == nil || len(manifest.Missing) != 1 {
		t.Fatalf("manifest after failure: %+v", manifest)
	}
}

type stubFile struct {
	data     string
	ext      string
	source   string
	complete bool
	detail   string
}

// stubProvider serves in-memory transcripts; it stands in for the database
// reader during collector tests.
type stubProvider struct {
	files map[string]stubFile
}

func (s *stubProvider) Open(_ context.Context, session corpus.Session) (corpus.OpenedTranscript, error) {
	file, ok := s.files[session.ID]
	if !ok {
		return corpus.OpenedTranscript{}, fmt.Errorf("no stub transcript for %s", session.ID)
	}
	return corpus.OpenedTranscript{
		Reader:   io.NopCloser(strings.NewReader(file.data)),
		Ext:      file.ext,
		Source:   file.source,
		Complete: file.complete,
		Detail:   file.detail,
	}, nil
}

func TestCollectUsesProviderAndFlagsPartial(t *testing.T) {
	provider := &stubProvider{files: map[string]stubFile{
		"s-complete": {data: "a\n", ext: ".jsonl", source: corpus.TranscriptExport, complete: true},
		"s-partial":  {data: "b\n", ext: ".jsonl", source: corpus.TranscriptExport, complete: false, detail: "capture status=incomplete"},
	}}
	pr := corpus.PullRequest{Repo: corpus.RepoSlug("peasant-labs/peasant"), Number: 9, HeadRef: "y"}
	bundles := []Bundle{{
		Assignment: sampler.Assignment{PR: pr, Split: sampler.Val},
		Sessions: []SessionTrace{
			{Session: corpus.Session{ID: "s-complete"}, Method: corpus.AttributionExact},
			{Session: corpus.Session{ID: "s-partial"}, Method: corpus.AttributionCommit},
		},
	}}

	datasetDir := filepath.Join(t.TempDir(), "dataset")
	manifest, err := Collect(context.Background(), datasetDir, bundles, Options{
		Transcripts:  provider,
		AllowMissing: true,
	})
	if err != nil {
		t.Fatalf("Collect: %v", err)
	}
	if len(manifest.Partial) != 1 || manifest.Partial[0] != "s-partial" {
		t.Fatalf("partial list %v", manifest.Partial)
	}
	if len(manifest.PRs) != 1 || manifest.PRs[0].Sessions != 2 {
		t.Fatalf("pr records %+v", manifest.PRs)
	}
	seen := map[string]Transcript{}
	for _, transcript := range manifest.PRs[0].Transcripts {
		seen[transcript.SessionID] = transcript
	}
	if !seen["s-partial"].Partial || seen["s-partial"].Source != corpus.TranscriptExport {
		t.Fatalf("partial transcript %+v", seen["s-partial"])
	}
	if seen["s-complete"].Partial || seen["s-complete"].Source != corpus.TranscriptExport {
		t.Fatalf("complete transcript %+v", seen["s-complete"])
	}
	for _, transcript := range seen {
		data, err := os.ReadFile(filepath.Join(datasetDir, transcript.Path))
		if err != nil {
			t.Fatalf("read %s: %v", transcript.Path, err)
		}
		if len(data) == 0 {
			t.Fatalf("empty transcript %s", transcript.Path)
		}
	}

	if _, err := Collect(context.Background(), filepath.Join(t.TempDir(), "strict"), bundles, Options{
		Transcripts: provider,
	}); err == nil {
		t.Fatal("Collect succeeded with a partial transcript and AllowMissing false")
	}
}
