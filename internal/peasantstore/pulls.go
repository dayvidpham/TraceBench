package peasantstore

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/dayvidpham/TraceBench/internal/corpus"

	_ "modernc.org/sqlite"
)

// LoadPulledTranscripts returns every transcript pulled from a village
// collective, newest pull first.
func LoadPulledTranscripts(ctx context.Context, dbPath string) ([]corpus.PulledTranscript, error) {
	db, err := sql.Open("sqlite", readOnlyURL(dbPath))
	if err != nil {
		return nil, fmt.Errorf("open peasant database %s: %w", dbPath, err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	rows, err := db.QueryContext(ctx, `
		SELECT village_host, transcript_id, owner_user_id, owner_username,
		       COALESCE(local_session_id, ''), COALESCE(title, ''), COALESCE(harness, ''),
		       COALESCE(project_name, ''), content_hash, visibility,
		       COALESCE(license_id, ''), pull_dir,
		       COALESCE(annotation_count, 0), first_pulled_at, last_pulled_at
		FROM pulled_transcripts
		ORDER BY last_pulled_at DESC, transcript_id`)
	if err != nil {
		return nil, fmt.Errorf("query pulled transcripts: %w", err)
	}
	defer rows.Close()

	var out []corpus.PulledTranscript
	for rows.Next() {
		var pull corpus.PulledTranscript
		if err := rows.Scan(
			&pull.VillageHost, &pull.TranscriptID, &pull.OwnerUserID, &pull.OwnerUsername,
			&pull.LocalSessionID, &pull.Title, &pull.Harness,
			&pull.ProjectName, &pull.ContentHash, &pull.Visibility,
			&pull.LicenseID, &pull.PullDir,
			&pull.AnnotationCount, &pull.FirstPulledAt, &pull.LastPulledAt,
		); err != nil {
			return nil, fmt.Errorf("scan pulled transcript: %w", err)
		}
		out = append(out, pull)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate pulled transcripts: %w", err)
	}
	return out, nil
}
