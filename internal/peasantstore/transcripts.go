package peasantstore

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

// CommitRef pairs a session with a commit hash observed while it ran.
type CommitRef struct {
	SessionID string
	Hash      string
}

// LoadSessionCommits returns every session-to-commit observation recorded in
// the database, from both the commit and association tables.
func LoadSessionCommits(ctx context.Context, dbPath string) ([]CommitRef, error) {
	db, err := sql.Open("sqlite", readOnlyURL(dbPath))
	if err != nil {
		return nil, fmt.Errorf("open peasant database %s: %w", dbPath, err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	rows, err := db.QueryContext(ctx, `
		SELECT session_id, commit_hash FROM session_commits
		UNION
		SELECT session_id, observed_commit_hash FROM session_commit_associations
		ORDER BY session_id, commit_hash`)
	if err != nil {
		return nil, fmt.Errorf("query session commits from %s: %w", dbPath, err)
	}
	defer rows.Close()

	var refs []CommitRef
	for rows.Next() {
		var ref CommitRef
		if err := rows.Scan(&ref.SessionID, &ref.Hash); err != nil {
			return nil, fmt.Errorf("scan session commit: %w", err)
		}
		refs = append(refs, ref)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate session commits: %w", err)
	}
	return refs, nil
}

// LoadSessionEdges returns resolved cross-session lineage edges from the
// relationship evidence table, such as a session started by or given context
// from another session.
func LoadSessionEdges(ctx context.Context, dbPath string) ([]corpus.SessionEdge, error) {
	db, err := sql.Open("sqlite", readOnlyURL(dbPath))
	if err != nil {
		return nil, fmt.Errorf("open peasant database %s: %w", dbPath, err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	rows, err := db.QueryContext(ctx, `
		SELECT session_id, target_local_id, kind
		FROM session_relationship_evidence
		WHERE target_state IN ('target_known', 'target_known_retained')
		  AND COALESCE(target_local_id, '') != ''
		ORDER BY session_id, kind, target_local_id`)
	if err != nil {
		return nil, fmt.Errorf("query session edges from %s: %w", dbPath, err)
	}
	defer rows.Close()

	var edges []corpus.SessionEdge
	for rows.Next() {
		var edge corpus.SessionEdge
		if err := rows.Scan(&edge.From, &edge.To, &edge.Kind); err != nil {
			return nil, fmt.Errorf("scan session edge: %w", err)
		}
		edges = append(edges, edge)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate session edges: %w", err)
	}
	return edges, nil
}

// TranscriptReader produces per-session transcripts. A bounded plain file
// source is copied byte-for-byte; anything else, including sessions whose
// raw source is a monolithic OpenCode database, is exported per entry from
// the Peasant full-content capture.
type TranscriptReader struct {
	db           *sql.DB
	MaxFileBytes int64
}

// OpenTranscriptReader opens dbPath read-only for transcript exports.
func OpenTranscriptReader(dbPath string, maxFileBytes int64) (*TranscriptReader, error) {
	if maxFileBytes <= 0 {
		return nil, errors.New("peasantstore: max transcript size must be positive")
	}
	db, err := sql.Open("sqlite", readOnlyURL(dbPath))
	if err != nil {
		return nil, fmt.Errorf("open peasant database %s: %w", dbPath, err)
	}
	db.SetMaxOpenConns(1)
	return &TranscriptReader{db: db, MaxFileBytes: maxFileBytes}, nil
}

// Close releases the database handle.
func (r *TranscriptReader) Close() error {
	if r == nil || r.db == nil {
		return nil
	}
	return r.db.Close()
}

// Open returns the transcript for one session.
func (r *TranscriptReader) Open(ctx context.Context, s corpus.Session) (corpus.OpenedTranscript, error) {
	if s.SourcePath != "" {
		if info, err := os.Stat(s.SourcePath); err == nil && info.Mode().IsRegular() {
			if info.Size() <= r.MaxFileBytes {
				sqlite, err := isSQLiteFile(s.SourcePath)
				if err != nil {
					return corpus.OpenedTranscript{}, fmt.Errorf("inspect source %s: %w", s.SourcePath, err)
				}
				if !sqlite {
					file, err := os.Open(s.SourcePath)
					if err != nil {
						return corpus.OpenedTranscript{}, fmt.Errorf("open source %s: %w", s.SourcePath, err)
					}
					return corpus.OpenedTranscript{
						Reader:   file,
						Ext:      transcriptExt(s.SourcePath),
						Source:   corpus.TranscriptRawFile,
						Complete: true,
					}, nil
				}
			}
		}
	}

	data, complete, detail, err := r.exportSession(ctx, s.ID)
	if err != nil {
		return corpus.OpenedTranscript{}, fmt.Errorf("export session %s: %w", s.ID, err)
	}
	return corpus.OpenedTranscript{
		Reader:   io.NopCloser(bytes.NewReader(data)),
		Ext:      ".jsonl",
		Source:   corpus.TranscriptExport,
		Complete: complete,
		Detail:   detail,
	}, nil
}

type exportedEntry struct {
	EntryIndex int    `json:"entry_index"`
	Provider   string `json:"provider"`
	Role       string `json:"role"`
	EntryType  string `json:"entry_type"`
	Timestamp  *int64 `json:"timestamp_ms,omitempty"`
	Content    string `json:"content"`
}

func (r *TranscriptReader) exportSession(ctx context.Context, sessionID string) ([]byte, bool, string, error) {
	var status, format string
	err := r.db.QueryRowContext(ctx,
		`SELECT status, capture_format FROM session_content_captures WHERE session_id = ?`,
		sessionID).Scan(&status, &format)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, false, "", errors.New("no content capture for session")
	}
	if err != nil {
		return nil, false, "", err
	}

	rows, err := r.db.QueryContext(ctx, `
		SELECT e.entry_index, e.provider, e.role, e.entry_type, e.timestamp_ms,
		       c.chunk_index, c.data
		FROM session_entries e
		JOIN session_entry_full_content_chunks c
		  ON c.session_id = e.session_id AND c.entry_index = e.entry_index
		WHERE e.session_id = ?
		ORDER BY e.entry_index, c.chunk_index`, sessionID)
	if err != nil {
		return nil, false, "", err
	}
	defer rows.Close()

	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	var index, timestampMS int64
	var provider, role, entryType string
	var content bytes.Buffer
	flush := func() error {
		if provider == "" && role == "" && content.Len() == 0 {
			return nil
		}
		entry := exportedEntry{
			EntryIndex: int(index),
			Provider:   provider,
			Role:       role,
			EntryType:  entryType,
			Content:    content.String(),
		}
		if timestampMS != 0 {
			entry.Timestamp = &timestampMS
		}
		if err := encoder.Encode(entry); err != nil {
			return err
		}
		return nil
	}

	entrySeen := false
	for rows.Next() {
		var (
			entryIndex int64
			ts         sql.NullInt64
			chunkIndex int
			data       []byte
			entryProv  string
			entryRole  string
			entryKind  string
		)
		if err := rows.Scan(&entryIndex, &entryProv, &entryRole, &entryKind, &ts, &chunkIndex, &data); err != nil {
			return nil, false, "", err
		}
		if chunkIndex == 0 && entrySeen && entryIndex != index {
			if err := flush(); err != nil {
				return nil, false, "", err
			}
			content.Reset()
		}
		if entryIndex != index || chunkIndex == 0 {
			index = entryIndex
			provider = entryProv
			role = entryRole
			entryType = entryKind
			timestampMS = 0
			if ts.Valid {
				timestampMS = ts.Int64
			}
			entrySeen = true
		}
		content.Write(data)
	}
	if err := rows.Err(); err != nil {
		return nil, false, "", err
	}
	if err := flush(); err != nil {
		return nil, false, "", err
	}

	complete := status == "complete" && format == "full"
	detail := fmt.Sprintf("capture status=%s format=%s", status, format)
	return buffer.Bytes(), complete, detail, nil
}

func transcriptExt(sourcePath string) string {
	ext := filepath.Ext(sourcePath)
	if ext == "" {
		return ".jsonl"
	}
	return ext
}

func isSQLiteFile(path string) (bool, error) {
	file, err := os.Open(path)
	if err != nil {
		return false, err
	}
	defer file.Close()
	header := make([]byte, 16)
	n, err := io.ReadFull(file, header)
	if err != nil && !errors.Is(err, io.ErrUnexpectedEOF) && !errors.Is(err, io.EOF) {
		return false, err
	}
	return n == len(header) && string(header) == "SQLite format 3\x00", nil
}
