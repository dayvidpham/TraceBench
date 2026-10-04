// Package peasantstore reads recorded agent sessions from a Peasant SQLite
// database. The database is opened read-only so sampling never disturbs the
// live Peasant state.
package peasantstore

import (
	"context"
	"database/sql"
	"fmt"
	"net/url"

	"github.com/dayvidpham/TraceBench/internal/corpus"

	_ "modernc.org/sqlite"
)

// readOnlyURL builds a SQLite DSN that refuses writes.
func readOnlyURL(dbPath string) string {
	u := url.URL{Scheme: "file", Path: dbPath}
	q := u.Query()
	q.Set("mode", "ro")
	u.RawQuery = q.Encode()
	return u.String()
}

// LoadSessions returns every session joined with its project metadata,
// ordered by start time.
func LoadSessions(ctx context.Context, dbPath string) ([]corpus.Session, error) {
	db, err := sql.Open("sqlite", readOnlyURL(dbPath))
	if err != nil {
		return nil, fmt.Errorf("open peasant database %s: %w", dbPath, err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	rows, err := db.QueryContext(ctx, `
		SELECT s.session_id, COALESCE(s.parent_id, ''), s.model_harness, s.model_id,
		       s.start_ms, s.end_ms,
		       COALESCE(s.git_branch, ''), COALESCE(s.git_worktree, ''),
		       COALESCE(s.session_cwd, ''), COALESCE(p.canonical_cwd, ''),
		       COALESCE(p.canonical_remote, ''), COALESCE(s.source_path, '')
		FROM sessions s
		JOIN projects p ON p.project_hash = s.project_hash
		ORDER BY s.start_ms, s.session_id`)
	if err != nil {
		return nil, fmt.Errorf("query sessions from %s: %w", dbPath, err)
	}
	defer rows.Close()

	var sessions []corpus.Session
	for rows.Next() {
		var s corpus.Session
		if err := rows.Scan(
			&s.ID, &s.ParentID, &s.Harness, &s.ModelID, &s.StartMS, &s.EndMS,
			&s.Branch, &s.Worktree, &s.SessionCwd, &s.ProjectCwd, &s.ProjectRemote,
			&s.SourcePath,
		); err != nil {
			return nil, fmt.Errorf("scan session row: %w", err)
		}
		sessions = append(sessions, s)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate sessions: %w", err)
	}
	return sessions, nil
}
