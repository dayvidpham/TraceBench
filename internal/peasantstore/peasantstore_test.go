package peasantstore

import (
	"context"
	"database/sql"
	"path/filepath"
	"testing"

	_ "modernc.org/sqlite"
)

func TestLoadSessions(t *testing.T) {
	dbPath := filepath.Join(t.TempDir(), "peasant.db")
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	schema := `
		CREATE TABLE projects (project_hash TEXT PRIMARY KEY, canonical_cwd TEXT, canonical_remote TEXT);
		CREATE TABLE sessions (
			session_id TEXT PRIMARY KEY, parent_id TEXT, model_harness TEXT, model_id TEXT,
			project_hash TEXT, start_ms INTEGER, end_ms INTEGER,
			git_branch TEXT, git_worktree TEXT, session_cwd TEXT, source_path TEXT
		);`
	if _, err := db.Exec(schema); err != nil {
		t.Fatal(err)
	}
	rows := [][]any{
		{"hash-a", "/repo/peasant", "github.com/peasant-labs/peasant"},
		{"hash-b", "/repo/other", nil},
	}
	for _, row := range rows {
		if _, err := db.Exec(`INSERT INTO projects VALUES (?, ?, ?)`, row...); err != nil {
			t.Fatal(err)
		}
	}
	sessions := [][]any{
		[]any{"s0", nil, "claude-code", "sonnet", "hash-a", 100, 150, "develop", "/repo/peasant/develop", "/repo/peasant", "/home/u/.claude/s0.jsonl"},
		[]any{"s1", "s0", "opencode", "gpt", "hash-a", 200, 250, nil, nil, nil, nil},
		[]any{"s2", nil, "claude-code", "sonnet", "hash-b", 300, 350, "main", "/repo/other", "/repo/other", "/home/u/.claude/s2.jsonl"},
	}
	for _, row := range sessions {
		if _, err := db.Exec(`INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`, row...); err != nil {
			t.Fatal(err)
		}
	}

	got, err := LoadSessions(context.Background(), dbPath)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 {
		t.Fatalf("loaded %d sessions, want 3", len(got))
	}
	if got[0].ID != "s0" || got[1].ID != "s1" || got[2].ID != "s2" {
		t.Fatalf("session order %s, %s, %s", got[0].ID, got[1].ID, got[2].ID)
	}
	if got[1].ParentID != "s0" {
		t.Fatalf("parent id %q, want s0", got[1].ParentID)
	}
	if got[1].Branch != "" || got[1].Worktree != "" || got[1].SourcePath != "" {
		t.Fatalf("null columns did not scan as empty: %+v", got[1])
	}
	if got[0].Harness != "claude-code" || got[0].EndMS != 150 {
		t.Fatalf("s0 fields: %+v", got[0])
	}
	if got[0].ProjectRemote != "github.com/peasant-labs/peasant" {
		t.Fatalf("project remote %q", got[0].ProjectRemote)
	}
	if got[0].StartMS != 100 || got[1].StartMS != 200 || got[2].StartMS != 300 {
		t.Fatalf("start times %d, %d, %d", got[0].StartMS, got[1].StartMS, got[2].StartMS)
	}
}
