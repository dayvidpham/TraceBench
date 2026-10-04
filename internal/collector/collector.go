// Package collector materializes sampled pull requests: it copies or exports
// each attributed session's transcript and writes the dataset manifest that
// records provenance for every file.
package collector

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/dayvidpham/TraceBench/internal/corpus"
	"github.com/dayvidpham/TraceBench/internal/sampler"
)

// TranscriptProvider opens a transcript for one session.
type TranscriptProvider interface {
	Open(ctx context.Context, s corpus.Session) (corpus.OpenedTranscript, error)
}

// Options controls transcript collection.
type Options struct {
	// AllowMissing keeps going when a session's transcript is missing or
	// incomplete. Without it, collection fails with an aggregated error.
	AllowMissing bool
	// Transcripts resolves per-session transcript sources. When nil, sessions
	// are copied from their source path as plain files.
	Transcripts TranscriptProvider
	// Targets records the requested split sizes in the manifest.
	Targets map[string]int
	// Database is the Peasant database the sessions were read from.
	Database string
	// Seed is the sampler seed used to select and split the dataset.
	Seed int64
}

// SessionTrace is a session selected for one pull request.
type SessionTrace struct {
	Session  corpus.Session
	Method   corpus.Attribution
	Relation string
}

// Bundle is one sampled pull request with its sessions.
type Bundle struct {
	Assignment sampler.Assignment
	Sessions   []SessionTrace
}

// Transcript records one collected transcript.
type Transcript struct {
	SessionID  string             `json:"session_id"`
	SourceFile string             `json:"source_file,omitempty"`
	Path       string             `json:"path,omitempty"`
	SHA256     string             `json:"sha256,omitempty"`
	Size       int64              `json:"size"`
	Source     string             `json:"source,omitempty"`
	Missing    bool               `json:"missing,omitempty"`
	Partial    bool               `json:"partial,omitempty"`
	Error      string             `json:"error,omitempty"`
	Detail     string             `json:"detail,omitempty"`
	Method     corpus.Attribution `json:"method,omitempty"`
	Relation   string             `json:"relation,omitempty"`
}

// SessionDetail is the session metadata stored beside a pull request. Local
// absolute paths are reduced to portable values: worktree directory names and
// source file basenames only.
type SessionDetail struct {
	ID            string             `json:"id"`
	ParentID      string             `json:"parent_id,omitempty"`
	Harness       string             `json:"harness"`
	ModelID       string             `json:"model_id"`
	StartedAt     time.Time          `json:"started_at"`
	EndedAt       time.Time          `json:"ended_at"`
	Branch        string             `json:"branch,omitempty"`
	Worktree      string             `json:"worktree,omitempty"`
	ProjectRemote string             `json:"project_remote,omitempty"`
	SourceFile    string             `json:"source_file,omitempty"`
	Method        corpus.Attribution `json:"method"`
	Relation      string             `json:"relation,omitempty"`
}

// PRRecord is one pull request in the dataset manifest. Body is the
// already-public pull request description and stays raw; see
// corpus.PullRequest for the redaction rationale.
type PRRecord struct {
	ID           string        `json:"id"`
	Split        sampler.Split `json:"split"`
	Repo         string        `json:"repo"`
	Number       int           `json:"number"`
	Title        string        `json:"title"`
	Body         string        `json:"body,omitempty"`
	URL          string        `json:"url"`
	Author       string        `json:"author,omitempty"`
	HeadRef      string        `json:"head_ref"`
	MergedAt     time.Time     `json:"merged_at"`
	Additions    int           `json:"additions"`
	Deletions    int           `json:"deletions"`
	LinesChanged int           `json:"lines_changed"`
	Group        string        `json:"group,omitempty"`
	Sessions     int           `json:"sessions"`
	Transcripts  []Transcript  `json:"transcripts"`
}

// prFile is the self-contained metadata document written per pull request.
type prFile struct {
	PRRecord
	SessionDetails []SessionDetail `json:"session_details"`
}

// Manifest is the dataset-level index.
type Manifest struct {
	SchemaVersion int            `json:"schema_version"`
	GeneratedAt   time.Time      `json:"generated_at"`
	Database      string         `json:"database,omitempty"`
	Seed          int64          `json:"seed"`
	Targets       map[string]int `json:"split_targets"`
	Counts        map[string]int `json:"split_counts"`
	Missing       []string       `json:"missing_transcripts,omitempty"`
	Partial       []string       `json:"partial_transcripts,omitempty"`
	PRs           []PRRecord     `json:"prs"`
}

// ManifestSchemaVersion is the current manifest layout version.
const ManifestSchemaVersion = 1

// Collect writes datasetDir. It returns the manifest it wrote; when
// transcripts are missing or incomplete and Options.AllowMissing is false, it
// returns the manifest together with an error naming every affected session.
func Collect(ctx context.Context, datasetDir string, bundles []Bundle, opts Options) (*Manifest, error) {
	if err := os.MkdirAll(datasetDir, 0o755); err != nil {
		return nil, fmt.Errorf("create dataset directory %s: %w", datasetDir, err)
	}

	manifest := &Manifest{
		SchemaVersion: ManifestSchemaVersion,
		GeneratedAt:   time.Now().UTC(),
		Database:      filepath.Base(opts.Database),
		Seed:          opts.Seed,
		Targets:       opts.Targets,
		Counts:        map[string]int{},
	}
	if manifest.Targets == nil {
		manifest.Targets = map[string]int{}
	}
	var problems []error
	homeDir, _ := os.UserHomeDir()

	sorted := append([]Bundle(nil), bundles...)
	sort.Slice(sorted, func(i, j int) bool {
		si, sj := string(sorted[i].Assignment.Split), string(sorted[j].Assignment.Split)
		if si != sj {
			return si < sj
		}
		return sorted[i].Assignment.PR.ID() < sorted[j].Assignment.PR.ID()
	})

	for _, bundle := range sorted {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		pr := bundle.Assignment.PR
		split := bundle.Assignment.Split
		manifest.Counts[string(split)]++

		dir := filepath.Join(datasetDir, string(split), pr.Repo.DirName(), fmt.Sprintf("pr-%04d", pr.Number))
		transcriptsDir := filepath.Join(dir, "transcripts")
		if err := os.MkdirAll(transcriptsDir, 0o755); err != nil {
			return nil, fmt.Errorf("create transcripts directory %s: %w", transcriptsDir, err)
		}

		record := PRRecord{
			ID:           pr.ID(),
			Split:        split,
			Repo:         string(pr.Repo),
			Number:       pr.Number,
			Title:        pr.Title,
			Body:         pr.Body,
			URL:          pr.URL,
			Author:       pr.Author,
			HeadRef:      pr.HeadRef,
			MergedAt:     pr.MergedAt,
			Additions:    pr.Additions,
			Deletions:    pr.Deletions,
			LinesChanged: pr.LinesChanged(),
			Group:        bundle.Assignment.Group,
			Sessions:     len(bundle.Sessions),
		}
		details := make([]SessionDetail, 0, len(bundle.Sessions))
		for _, trace := range bundle.Sessions {
			session := trace.Session
			details = append(details, SessionDetail{
				ID: session.ID, ParentID: session.ParentID, Harness: session.Harness,
				ModelID: session.ModelID, StartedAt: time.UnixMilli(session.StartMS).UTC(),
				EndedAt: session.End(), Branch: session.Branch,
				Worktree: filepath.Base(session.Worktree), ProjectRemote: session.ProjectRemote,
				SourceFile: filepath.Base(session.SourcePath), Method: trace.Method,
				Relation: trace.Relation,
			})
			transcript := collectTranscript(ctx, transcriptsDir, datasetDir, session, trace.Method, trace.Relation, opts.Transcripts)
			transcript.Error = scrubPaths(transcript.Error, homeDir, session.SourcePath, opts.Database)
			transcript.Detail = scrubPaths(transcript.Detail, homeDir, session.SourcePath, opts.Database)
			if transcript.Missing {
				if !opts.AllowMissing {
					problems = append(problems, fmt.Errorf("session %s (pull request %s): %s",
						session.ID, pr.ID(), problemMessage(transcript)))
				}
			} else if transcript.Partial {
				manifest.Partial = append(manifest.Partial, session.ID)
				if !opts.AllowMissing {
					problems = append(problems, fmt.Errorf("session %s (pull request %s): %s",
						session.ID, pr.ID(), problemMessage(transcript)))
				}
			}
			record.Transcripts = append(record.Transcripts, transcript)
		}
		record.SortTranscripts()
		manifest.PRs = append(manifest.PRs, record)
		if err := writeJSON(filepath.Join(dir, "pr.json"), prFile{PRRecord: record, SessionDetails: details}); err != nil {
			return nil, err
		}
	}

	for _, record := range manifest.PRs {
		for _, transcript := range record.Transcripts {
			if transcript.Missing {
				manifest.Missing = append(manifest.Missing, transcript.SessionID)
			}
		}
	}
	if err := writeJSON(filepath.Join(datasetDir, "manifest.json"), manifest); err != nil {
		return manifest, err
	}
	if len(problems) == 0 {
		return manifest, nil
	}
	return manifest, fmt.Errorf("collect transcripts: %w", errors.Join(problems...))
}

func problemMessage(transcript Transcript) string {
	if transcript.Error != "" {
		return transcript.Error
	}
	if transcript.Detail != "" {
		return transcript.Detail
	}
	return "transcript unavailable"
}

// scrubPaths replaces known local roots in diagnostic text with portable
// forms so dataset metadata never carries machine-specific paths.
func scrubPaths(message, homeDir string, roots ...string) string {
	if message == "" {
		return message
	}
	for _, root := range roots {
		if root == "" {
			continue
		}
		message = strings.ReplaceAll(message, root, filepath.Base(root))
	}
	if homeDir != "" {
		message = strings.ReplaceAll(message, homeDir, "~")
	}
	return message
}

// SortTranscripts orders transcripts by session id for stable output.
func (r *PRRecord) SortTranscripts() {
	sort.Slice(r.Transcripts, func(i, j int) bool {
		return r.Transcripts[i].SessionID < r.Transcripts[j].SessionID
	})
}

func collectTranscript(
	ctx context.Context,
	transcriptsDir, datasetDir string,
	session corpus.Session,
	method corpus.Attribution,
	relation string,
	provider TranscriptProvider,
) Transcript {
	transcript := Transcript{
		SessionID:  session.ID,
		SourceFile: filepath.Base(session.SourcePath),
		Method:     method,
		Relation:   relation,
	}
	opened, err := openTranscript(ctx, session, provider)
	if err != nil {
		transcript.Missing = true
		transcript.Error = err.Error()
		return transcript
	}
	defer opened.Reader.Close()

	name := session.ID + opened.Ext
	dstPath := filepath.Join(transcriptsDir, name)
	dst, err := os.Create(dstPath)
	if err != nil {
		transcript.Missing = true
		transcript.Error = err.Error()
		return transcript
	}
	hasher := sha256.New()
	written, copyErr := io.Copy(io.MultiWriter(dst, hasher), opened.Reader)
	closeErr := dst.Close()
	if copyErr != nil || closeErr != nil {
		transcript.Missing = true
		if copyErr != nil {
			transcript.Error = copyErr.Error()
		} else {
			transcript.Error = closeErr.Error()
		}
		if removeErr := os.Remove(dstPath); removeErr != nil {
			transcript.Error += "; remove partial file: " + removeErr.Error()
		}
		return transcript
	}

	relPath, err := filepath.Rel(datasetDir, dstPath)
	if err != nil {
		relPath = name
	}
	transcript.Path = filepath.ToSlash(relPath)
	transcript.Size = written
	transcript.SHA256 = hex.EncodeToString(hasher.Sum(nil))
	transcript.Source = opened.Source
	transcript.Detail = opened.Detail
	transcript.Partial = !opened.Complete
	return transcript
}

func openTranscript(ctx context.Context, session corpus.Session, provider TranscriptProvider) (corpus.OpenedTranscript, error) {
	if provider == nil {
		if session.SourcePath == "" {
			return corpus.OpenedTranscript{}, errors.New("session has no source path")
		}
		file, err := os.Open(session.SourcePath)
		if err != nil {
			return corpus.OpenedTranscript{}, err
		}
		ext := filepath.Ext(session.SourcePath)
		if ext == "" {
			ext = ".jsonl"
		}
		return corpus.OpenedTranscript{
			Reader:   file,
			Ext:      ext,
			Source:   corpus.TranscriptRawFile,
			Complete: true,
		}, nil
	}
	return provider.Open(ctx, session)
}

// LoadManifest reads a dataset manifest written by Collect.
func LoadManifest(path string) (*Manifest, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var manifest Manifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return nil, fmt.Errorf("decode %s: %w", path, err)
	}
	return &manifest, nil
}

func writeJSON(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return fmt.Errorf("encode %s: %w", path, err)
	}
	data = append(data, '\n')
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return fmt.Errorf("write %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("replace %s: %w", path, err)
	}
	return nil
}
