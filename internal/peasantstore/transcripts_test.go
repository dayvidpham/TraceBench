package peasantstore

import (
	"context"
	"database/sql"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/dayvidpham/TraceBench/internal/corpus"

	_ "modernc.org/sqlite"
)

func seedTranscriptDB(t *testing.T) string {
	t.Helper()
	dbPath := filepath.Join(t.TempDir(), "peasant.db")
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	schema := `
		CREATE TABLE session_content_captures (
			session_id TEXT PRIMARY KEY, status TEXT NOT NULL, source_authority TEXT NOT NULL,
			transcript_origin INTEGER NOT NULL DEFAULT 0, capture_format TEXT NOT NULL,
			entry_count INTEGER NOT NULL DEFAULT 0, content_row_count INTEGER NOT NULL DEFAULT 0,
			full_capture_sha256 TEXT, captured_at_ms INTEGER NOT NULL, failure_code TEXT,
			failure_message TEXT, publication_capture_revision INTEGER NOT NULL DEFAULT 0
		);
		CREATE TABLE session_entries (
			session_id TEXT NOT NULL, entry_index INTEGER NOT NULL, provider TEXT NOT NULL,
			entry_type TEXT NOT NULL, role TEXT NOT NULL, timestamp_ms INTEGER,
			content_preview TEXT, PRIMARY KEY (session_id, entry_index)
		);
		CREATE TABLE session_entry_full_content (
			session_id TEXT NOT NULL, entry_index INTEGER NOT NULL, full_byte_length INTEGER NOT NULL,
			full_sha256 TEXT NOT NULL, preview_byte_length INTEGER NOT NULL, preview_sha256 TEXT NOT NULL,
			preview_is_full INTEGER NOT NULL, chunk_count INTEGER NOT NULL, captured_at_ms INTEGER NOT NULL,
			PRIMARY KEY (session_id, entry_index)
		);
		CREATE TABLE session_entry_full_content_chunks (
			session_id TEXT NOT NULL, entry_index INTEGER NOT NULL, chunk_index INTEGER NOT NULL,
			byte_offset INTEGER NOT NULL, byte_length INTEGER NOT NULL, chunk_sha256 TEXT NOT NULL,
			data BLOB NOT NULL, PRIMARY KEY (session_id, entry_index, chunk_index)
		);
		CREATE TABLE session_commits (
			session_id TEXT NOT NULL, commit_hash TEXT NOT NULL, PRIMARY KEY (session_id, commit_hash)
		);
		CREATE TABLE session_commit_associations (
			association_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
			observed_commit_hash TEXT NOT NULL, subject TEXT
		);`
	if _, err := db.Exec(schema); err != nil {
		t.Fatal(err)
	}

	insert := func(query string, args ...any) {
		t.Helper()
		if _, err := db.Exec(query, args...); err != nil {
			t.Fatal(err)
		}
	}
	// Complete capture with two entries, one split across two chunks.
	insert(`INSERT INTO session_content_captures
		(session_id, status, source_authority, capture_format, captured_at_ms)
		VALUES ('s-export', 'complete', 'new_ingest', 'full', 1)`)
	insert(`INSERT INTO session_entry_full_content VALUES ('s-export', 0, 11, '', 11, '', 1, 2, 1)`)
	insert(`INSERT INTO session_entry_full_content VALUES ('s-export', 1, 11, '', 11, '', 1, 1, 1)`)
	insert(`INSERT INTO session_entries VALUES ('s-export', 0, 'opencode', 'text', 'user', 1000, 'hello')`)
	insert(`INSERT INTO session_entries VALUES ('s-export', 1, 'opencode', 'tool_use', 'assistant', 2000, 'second')`)
	insert(`INSERT INTO session_entry_full_content_chunks VALUES ('s-export', 0, 0, 0, 6, '', 'hello ')`)
	insert(`INSERT INTO session_entry_full_content_chunks VALUES ('s-export', 0, 1, 6, 5, '', 'world')`)
	insert(`INSERT INTO session_entry_full_content_chunks VALUES ('s-export', 1, 0, 0, 11, '', 'second'||char(10)||'line')`)
	// Incomplete capture.
	insert(`INSERT INTO session_content_captures
		(session_id, status, source_authority, capture_format, captured_at_ms)
		VALUES ('s-partial', 'incomplete', 'provider_source', 'preview_only', 1)`)
	insert(`INSERT INTO session_entry_full_content VALUES ('s-partial', 0, 4, '', 4, '', 1, 1, 1)`)
	insert(`INSERT INTO session_entries VALUES ('s-partial', 0, 'opencode', 'text', 'user', 3000, 'preview')`)
	insert(`INSERT INTO session_entry_full_content_chunks VALUES ('s-partial', 0, 0, 0, 4, '', 'prev')`)
	// Session commits from both tables; one duplicate.
	insert(`INSERT INTO session_commits VALUES ('s1', 'aaaa')`)
	insert(`INSERT INTO session_commits VALUES ('s1', 'bbbb')`)
	insert(`INSERT INTO session_commit_associations VALUES ('assoc-1', 's1', 'bbbb', 'subject')`)
	insert(`INSERT INTO session_commit_associations VALUES ('assoc-2', 's2', 'cccc', 'subject')`)
	return dbPath
}

func TestLoadSessionCommits(t *testing.T) {
	dbPath := seedTranscriptDB(t)
	refs, err := LoadSessionCommits(context.Background(), dbPath)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string][]string{}
	for _, ref := range refs {
		got[ref.SessionID] = append(got[ref.SessionID], ref.Hash)
	}
	want := map[string][]string{"s1": {"aaaa", "bbbb"}, "s2": {"cccc"}}
	if len(got) != len(want) {
		t.Fatalf("loaded commits %v, want %v", got, want)
	}
	for session, hashes := range want {
		if strings.Join(got[session], ",") != strings.Join(hashes, ",") {
			t.Fatalf("commits for %s = %v, want %v", session, got[session], hashes)
		}
	}
}

func TestTranscriptReaderExportsDatabaseContent(t *testing.T) {
	dbPath := seedTranscriptDB(t)
	reader, err := OpenTranscriptReader(dbPath, 1<<20)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	ctx := context.Background()

	// A source path pointing at a SQLite database falls back to an export.
	opened, err := reader.Open(ctx, corpus.Session{ID: "s-export", SourcePath: dbPath})
	if err != nil {
		t.Fatal(err)
	}
	defer opened.Reader.Close()
	if opened.Source != corpus.TranscriptExport || !opened.Complete || opened.Ext != ".jsonl" {
		t.Fatalf("opened %+v", opened)
	}
	data, err := io.ReadAll(opened.Reader)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSuffix(string(data), "\n"), "\n")
	if len(lines) != 2 {
		t.Fatalf("export produced %d lines, want 2: %q", len(lines), data)
	}
	type entry struct {
		EntryIndex int    `json:"entry_index"`
		Provider   string `json:"provider"`
		Role       string `json:"role"`
		EntryType  string `json:"entry_type"`
		Timestamp  *int64 `json:"timestamp_ms"`
		Content    string `json:"content"`
	}
	var first, second entry
	if err := json.Unmarshal([]byte(lines[0]), &first); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(lines[1]), &second); err != nil {
		t.Fatal(err)
	}
	if first.Content != "hello world" || first.EntryIndex != 0 || first.Role != "user" {
		t.Fatalf("first entry %+v", first)
	}
	if first.Timestamp == nil || *first.Timestamp != 1000 {
		t.Fatalf("first timestamp %+v", first.Timestamp)
	}
	if second.Content != "second\nline" || second.EntryType != "tool_use" {
		t.Fatalf("second entry %+v", second)
	}

	// An incomplete capture exports but is flagged incomplete.
	partial, err := reader.Open(ctx, corpus.Session{ID: "s-partial"})
	if err != nil {
		t.Fatal(err)
	}
	defer partial.Reader.Close()
	if partial.Complete || !strings.Contains(partial.Detail, "preview_only") {
		t.Fatalf("partial %+v", partial)
	}

	// A session without any capture fails closed.
	if _, err := reader.Open(ctx, corpus.Session{ID: "s-none"}); err == nil {
		t.Fatal("expected an error for a session without a capture")
	}
}

func TestTranscriptReaderCopiesPlainFiles(t *testing.T) {
	dir := t.TempDir()
	sourcePath := filepath.Join(dir, "session.jsonl")
	content := []byte("{}\n")
	if err := os.WriteFile(sourcePath, content, 0o644); err != nil {
		t.Fatal(err)
	}
	reader, err := OpenTranscriptReader(filepath.Join(dir, "peasant.db"), 1<<20)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()

	opened, err := reader.Open(context.Background(), corpus.Session{ID: "s1", SourcePath: sourcePath})
	if err != nil {
		t.Fatal(err)
	}
	defer opened.Reader.Close()
	if opened.Source != corpus.TranscriptRawFile || !opened.Complete || opened.Ext != ".jsonl" {
		t.Fatalf("opened %+v", opened)
	}
	data, err := io.ReadAll(opened.Reader)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != string(content) {
		t.Fatalf("content %q, want %q", data, content)
	}
}
