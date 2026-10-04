package peasantstore

import (
	"context"
	"database/sql"
	"fmt"
	"path/filepath"
	"sort"
	"strings"

	"github.com/dayvidpham/TraceBench/internal/corpus"

	_ "modernc.org/sqlite"
)

// LoadSessionArtifacts returns the requested sessions enriched with project,
// host, metric, commit, and relationship evidence. Requested ids that do not
// exist are skipped.
func LoadSessionArtifacts(ctx context.Context, dbPath string, ids []string) ([]corpus.SessionArtifact, error) {
	wanted := make(map[string]bool, len(ids))
	for _, id := range ids {
		wanted[id] = true
	}
	if len(wanted) == 0 {
		return nil, nil
	}

	db, err := sql.Open("sqlite", readOnlyURL(dbPath))
	if err != nil {
		return nil, fmt.Errorf("open peasant database %s: %w", dbPath, err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	artifacts, err := loadArtifactRows(ctx, db, wanted)
	if err != nil {
		return nil, err
	}
	if err := loadArtifactMetrics(ctx, db, artifacts); err != nil {
		return nil, err
	}
	if err := loadArtifactCommits(ctx, db, artifacts); err != nil {
		return nil, err
	}
	if err := loadArtifactRelationships(ctx, db, artifacts); err != nil {
		return nil, err
	}
	if err := loadArtifactChildren(ctx, db, artifacts); err != nil {
		return nil, err
	}

	out := make([]corpus.SessionArtifact, 0, len(artifacts))
	for _, artifact := range artifacts {
		out = append(out, *artifact)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].StartMS != out[j].StartMS {
			return out[i].StartMS < out[j].StartMS
		}
		return out[i].ID < out[j].ID
	})
	return out, nil
}

func loadArtifactRows(ctx context.Context, db *sql.DB, wanted map[string]bool) (map[string]*corpus.SessionArtifact, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT s.session_id, COALESCE(s.parent_id, ''), s.model_harness, s.model_id,
		       s.start_ms, s.end_ms, s.ingested_ms,
		       COALESCE(s.git_branch, ''), COALESCE(s.git_worktree, ''), COALESCE(s.session_cwd, ''),
		       COALESCE(p.canonical_cwd, ''), COALESCE(p.canonical_remote, ''), COALESCE(s.source_path, ''),
		       s.source_format, COALESCE(s.git_tracking, ''), COALESCE(s.tool_version, ''),
		       COALESCE(s.root_session_id, ''), COALESCE(s.session_purpose, ''),
		       COALESCE(s.adapter_version, 0), COALESCE(s.license_id, ''),
		       COALESCE(s.project_hash, ''), COALESCE(h.host_slug, ''), s.input_submission_count
		FROM sessions s
		JOIN projects p ON p.project_hash = s.project_hash
		LEFT JOIN host_slugs h ON h.opaque_id = s.opaque_host_id
		ORDER BY s.start_ms, s.session_id`)
	if err != nil {
		return nil, fmt.Errorf("query session artifacts: %w", err)
	}
	defer rows.Close()

	artifacts := map[string]*corpus.SessionArtifact{}
	for rows.Next() {
		var a corpus.SessionArtifact
		var submission sql.NullInt64
		if err := rows.Scan(
			&a.ID, &a.ParentID, &a.Harness, &a.ModelID, &a.StartMS, &a.EndMS, &a.IngestedMS,
			&a.Branch, &a.Worktree, &a.SessionCwd,
			&a.ProjectCwd, &a.ProjectRemote, &a.SourcePath,
			&a.SourceFormat, &a.GitTracking, &a.ToolVersion,
			&a.RootSessionID, &a.SessionPurpose, &a.AdapterVersion, &a.LicenseID,
			&a.ProjectHash, &a.HostSlug, &submission,
		); err != nil {
			return nil, fmt.Errorf("scan session artifact: %w", err)
		}
		if submission.Valid {
			a.InputSubmissionCount = &submission.Int64
		}
		a.ProjectName = projectName(a.ProjectRemote, a.ProjectCwd)
		if wanted[a.ID] {
			artifacts[a.ID] = &a
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate session artifacts: %w", err)
	}
	return artifacts, nil
}

func projectName(remote, cwd string) string {
	if remote != "" {
		trimmed := strings.TrimSuffix(remote, ".git")
		if idx := strings.LastIndex(trimmed, "/"); idx >= 0 && idx+1 < len(trimmed) {
			return trimmed[idx+1:]
		}
	}
	if cwd != "" {
		return filepath.Base(cwd)
	}
	return ""
}

func loadArtifactMetrics(ctx context.Context, db *sql.DB, artifacts map[string]*corpus.SessionArtifact) error {
	rows, err := db.QueryContext(ctx, `
		SELECT session_id,
		       COALESCE(turn_count, 0), COALESCE(subagent_count, 0), COALESCE(tool_calls, 0),
		       COALESCE(total_tokens, 0), COALESCE(input_tokens, 0), COALESCE(output_tokens, 0),
		       COALESCE(duration_minutes, 0), COALESCE(outcome, '')
		FROM session_metrics`)
	if err != nil {
		return fmt.Errorf("query session metrics: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var id string
		var turn, subagent, toolCalls, total, in, out int
		var minutes float64
		var outcome string
		if err := rows.Scan(&id, &turn, &subagent, &toolCalls, &total, &in, &out, &minutes, &outcome); err != nil {
			return fmt.Errorf("scan session metrics: %w", err)
		}
		if a, ok := artifacts[id]; ok {
			a.TurnCount = turn
			a.SubagentCount = subagent
			a.ToolCalls = toolCalls
			a.TotalTokens = total
			a.InputTokens = in
			a.OutputTokens = out
			a.DurationMinutes = minutes
			a.Outcome = outcome
		}
	}
	return rows.Err()
}

func loadArtifactCommits(ctx context.Context, db *sql.DB, artifacts map[string]*corpus.SessionArtifact) error {
	rows, err := db.QueryContext(ctx, `
		SELECT session_id, commit_hash, COALESCE(message, ''), COALESCE(author_name, ''),
		       COALESCE(author_email, ''), COALESCE(commit_time, 0), COALESCE(author_time, 0)
		FROM session_commits`)
	if err != nil {
		return fmt.Errorf("query session commits: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var id string
		var record corpus.CommitRecord
		if err := rows.Scan(&id, &record.Hash, &record.Message, &record.AuthorName,
			&record.AuthorEmail, &record.CommitTime, &record.AuthorTime); err != nil {
			return fmt.Errorf("scan session commit: %w", err)
		}
		if a, ok := artifacts[id]; ok {
			a.Commits = append(a.Commits, record)
		}
	}
	if err := rows.Err(); err != nil {
		return err
	}

	assocRows, err := db.QueryContext(ctx, `
		SELECT session_id, association_id, observed_commit_hash, COALESCE(subject, ''), COALESCE(author_time, 0)
		FROM session_commit_associations`)
	if err != nil {
		return fmt.Errorf("query session commit associations: %w", err)
	}
	defer assocRows.Close()
	for assocRows.Next() {
		var id string
		var record corpus.AssociationRecord
		if err := assocRows.Scan(&id, &record.ID, &record.ObservedCommitHash, &record.Subject, &record.AuthorTime); err != nil {
			return fmt.Errorf("scan session commit association: %w", err)
		}
		if a, ok := artifacts[id]; ok {
			a.Associations = append(a.Associations, record)
		}
	}
	return assocRows.Err()
}

func loadArtifactRelationships(ctx context.Context, db *sql.DB, artifacts map[string]*corpus.SessionArtifact) error {
	rows, err := db.QueryContext(ctx, `
		SELECT session_id, kind, target_state, COALESCE(target_local_id, ''), evidence
		FROM session_relationship_evidence`)
	if err != nil {
		return fmt.Errorf("query session relationships: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var id string
		var record corpus.RelationshipRecord
		if err := rows.Scan(&id, &record.Kind, &record.TargetState, &record.TargetLocalID, &record.Evidence); err != nil {
			return fmt.Errorf("scan session relationship: %w", err)
		}
		if a, ok := artifacts[id]; ok {
			a.Relationships = append(a.Relationships, record)
		}
	}
	return rows.Err()
}

func loadArtifactChildren(ctx context.Context, db *sql.DB, artifacts map[string]*corpus.SessionArtifact) error {
	rows, err := db.QueryContext(ctx, `
		SELECT session_id, parent_id FROM sessions
		WHERE parent_id IS NOT NULL AND parent_id != ''`)
	if err != nil {
		return fmt.Errorf("query session children: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var child, parent string
		if err := rows.Scan(&child, &parent); err != nil {
			return fmt.Errorf("scan session child: %w", err)
		}
		if a, ok := artifacts[parent]; ok {
			a.SubagentIDs = append(a.SubagentIDs, child)
		}
	}
	if err := rows.Err(); err != nil {
		return err
	}
	for _, a := range artifacts {
		sort.Strings(a.SubagentIDs)
	}
	return nil
}
