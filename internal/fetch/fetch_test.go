package fetch

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/peasant-labs/schema"

	"github.com/dayvidpham/TraceBench/internal/dump"
)

func testDataset(t *testing.T) map[string][]byte {
	t.Helper()
	transcripts := map[string]string{
		"11111111-1111-1111-1111-111111111111": `{"contractVersion":"0.1.1","kind":"session_detail","sessionDetail":{"id":"11111111-1111-1111-1111-111111111111"}}`,
		"22222222-2222-2222-2222-222222222222": `{"contractVersion":"0.1.1","kind":"session_detail","sessionDetail":{"id":"22222222-2222-2222-2222-222222222222"}}`,
	}
	files := map[string][]byte{}
	var metadata strings.Builder
	for id, payload := range transcripts {
		meta := schema.NewUnifiedMetadata()
		meta.SessionID = schema.SessionID(id)
		meta.ModelHarness = "claude-code"
		meta.Source = schema.SourceInfo{Format: "jsonl"}
		meta.ContentHash = schema.ComputeTranscriptHash([]byte(payload))
		line, err := json.Marshal(&meta)
		if err != nil {
			t.Fatal(err)
		}
		metadata.Write(append(line, '\n'))
		files["transcripts/"+id+".jsonl"] = []byte(payload)
	}
	manifest, err := json.Marshal(dump.Manifest{SchemaVersion: 1, Sessions: 2, Traces: 3})
	if err != nil {
		t.Fatal(err)
	}
	files["manifest.json"] = manifest
	files["metadata.jsonl"] = []byte(metadata.String())
	files["pull_requests.jsonl"] = []byte(`{"id":"owner/repo#1"}` + "\n")
	files["traces.jsonl"] = []byte(
		`{"pr":"owner/repo#1","session_id":"11111111-1111-1111-1111-111111111111","method":"exact","relation":"linked","split":"train"}` + "\n" +
			`{"pr":"owner/repo#1","session_id":"22222222-2222-2222-2222-222222222222","method":"context","relation":"ancestor","split":"train"}` + "\n" +
			`{"pr":"owner/repo#1","session_id":"11111111-1111-1111-1111-111111111111","method":"commit","split":"val"}` + "\n")
	return files
}

func testServer(t *testing.T, files map[string][]byte) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		const prefix = "/datasets/test/repo/resolve/main/"
		if !strings.HasPrefix(r.URL.Path, prefix) {
			http.NotFound(w, r)
			return
		}
		data, ok := files[strings.TrimPrefix(r.URL.Path, prefix)]
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.Write(data)
	}))
	t.Cleanup(server.Close)
	return server
}

func TestFetchDownloadsAndVerifies(t *testing.T) {
	files := testDataset(t)
	server := testServer(t, files)
	dir := filepath.Join(t.TempDir(), "hf")

	result, err := Fetch(context.Background(), Options{
		Repo: "test/repo", Endpoint: server.URL, Dir: dir, Verify: true, Concurrency: 2,
	})
	if err != nil {
		t.Fatalf("Fetch: %v", err)
	}
	if result.Sessions != 2 || result.Traces != 3 || result.Verified != 2 {
		t.Fatalf("result %+v", result)
	}
	for _, name := range []string{"manifest.json", "metadata.jsonl", "pull_requests.jsonl", "traces.jsonl"} {
		if _, err := os.Stat(filepath.Join(dir, name)); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
	}
	for id := range map[string]bool{
		"11111111-1111-1111-1111-111111111111": true,
		"22222222-2222-2222-2222-222222222222": true,
	} {
		data, err := os.ReadFile(filepath.Join(dir, "transcripts", id+".jsonl"))
		if err != nil {
			t.Fatalf("transcript %s: %v", id, err)
		}
		if !strings.Contains(string(data), id) {
			t.Fatalf("transcript %s content %q", id, data)
		}
	}
}

func TestFetchFailsOnHashMismatch(t *testing.T) {
	files := testDataset(t)
	files["transcripts/11111111-1111-1111-1111-111111111111.jsonl"] = []byte("tampered")
	server := testServer(t, files)

	_, err := Fetch(context.Background(), Options{
		Repo: "test/repo", Endpoint: server.URL, Dir: filepath.Join(t.TempDir(), "hf"), Verify: true,
	})
	if err == nil || !strings.Contains(err.Error(), "hash mismatch") {
		t.Fatalf("Fetch error %v", err)
	}
}

func TestFetchFailsOnMissingTranscript(t *testing.T) {
	files := testDataset(t)
	delete(files, "transcripts/22222222-2222-2222-2222-222222222222.jsonl")
	server := testServer(t, files)

	_, err := Fetch(context.Background(), Options{
		Repo: "test/repo", Endpoint: server.URL, Dir: filepath.Join(t.TempDir(), "hf"), Verify: true,
	})
	if err == nil || !strings.Contains(err.Error(), "404") {
		t.Fatalf("Fetch error %v", err)
	}
}

func TestFetchFailsOnSessionCountMismatch(t *testing.T) {
	files := testDataset(t)
	files["manifest.json"] = []byte(`{"schema_version":1,"sessions":99,"traces":3}`)
	server := testServer(t, files)

	_, err := Fetch(context.Background(), Options{
		Repo: "test/repo", Endpoint: server.URL, Dir: filepath.Join(t.TempDir(), "hf"), Verify: true,
	})
	if err == nil || !strings.Contains(err.Error(), "manifest lists 99 sessions") {
		t.Fatalf("Fetch error %v", err)
	}
}
