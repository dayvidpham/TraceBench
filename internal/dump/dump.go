package dump

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/peasant-labs/redact"
	"github.com/peasant-labs/schema"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

// ManifestSchemaVersion is the dump manifest layout version.
const ManifestSchemaVersion = 1

// Source names a dump input.
type Source string

const (
	// SourceLocal builds the dump from the sampled local dataset.
	SourceLocal Source = "local"
	// SourceVillagePull builds the dump from transcripts pulled from a
	// village collective.
	SourceVillagePull Source = "village-pull"
)

// Options controls dump writing.
type Options struct {
	Source              Source
	Database            string
	PeasantBin          string
	PeasantDataDir      string
	PushContractVersion schema.PushContractVersion
	RedactionLevel      redact.RedactionLevel
	XDG                 redact.XDGPaths
	AllowMissing        bool
	Seed                int64
	Targets             map[string]int
	SplitCounts         map[string]int
	Now                 func() time.Time
}

// PRRecord is one sampled pull request in the flat dump index.
type PRRecord struct {
	ID             string    `json:"id"`
	Repo           string    `json:"repo"`
	Number         int       `json:"number"`
	Title          string    `json:"title"`
	URL            string    `json:"url"`
	Author         string    `json:"author,omitempty"`
	HeadRef        string    `json:"head_ref"`
	MergedAt       time.Time `json:"merged_at"`
	Additions      int       `json:"additions"`
	Deletions      int       `json:"deletions"`
	LinesChanged   int       `json:"lines_changed"`
	Split          string    `json:"split"`
	Group          string    `json:"group,omitempty"`
	LinkedSessions int       `json:"linked_sessions"`
	TotalSessions  int       `json:"total_sessions"`
	DumpedSessions int       `json:"dumped_sessions"`
}

// TraceRecord links one pull request to one session.
type TraceRecord struct {
	PR        string `json:"pr"`
	SessionID string `json:"session_id"`
	Method    string `json:"method"`
	Relation  string `json:"relation,omitempty"`
}

// FailedSession records a session that could not be dumped.
type FailedSession struct {
	SessionID string `json:"session_id"`
	Error     string `json:"error"`
}

// Manifest describes a written dump.
type Manifest struct {
	SchemaVersion           int             `json:"schema_version"`
	GeneratedAt             time.Time       `json:"generated_at"`
	Source                  string          `json:"source"`
	MetadataSchemaVersion   int             `json:"metadata_schema_version"`
	PushContractVersion     string          `json:"push_contract_version"`
	RedactionLevel          string          `json:"redaction_level"`
	RedactionRuleSetVersion string          `json:"redaction_rule_set_version"`
	Database                string          `json:"database,omitempty"`
	Seed                    int64           `json:"seed,omitempty"`
	SplitTargets            map[string]int  `json:"split_targets,omitempty"`
	SplitCounts             map[string]int  `json:"split_counts,omitempty"`
	PullRequests            int             `json:"pull_requests"`
	Traces                  int             `json:"traces"`
	UnresolvedTraces        int             `json:"unresolved_traces,omitempty"`
	Sessions                int             `json:"sessions"`
	VillagePulls            int             `json:"village_pulls,omitempty"`
	FailedSessions          []FailedSession `json:"failed_sessions,omitempty"`
}

// Writer writes dump artifacts into a directory.
type Writer struct {
	dir      string
	opts     Options
	redactor redact.Redactor
	metadata bytes.Buffer
	written  map[string]bool
	manifest *Manifest
	now      func() time.Time
}

// NewWriter prepares dir for writing and constructs the redaction pipeline.
func NewWriter(dir string, opts Options) (*Writer, error) {
	if opts.PushContractVersion == "" {
		opts.PushContractVersion = schema.PushContractVersion("0.1.1")
	}
	if opts.RedactionLevel == "" {
		opts.RedactionLevel = redact.Standard
	}
	if opts.Now == nil {
		opts.Now = time.Now
	}
	redactor, err := redact.NewRedactor(opts.RedactionLevel, nil, opts.XDG)
	if err != nil {
		return nil, fmt.Errorf("create redactor: %w", err)
	}
	transcriptsDir := filepath.Join(dir, "transcripts")
	if err := os.MkdirAll(transcriptsDir, 0o755); err != nil {
		return nil, fmt.Errorf("create transcripts directory %s: %w", transcriptsDir, err)
	}
	now := opts.Now().UTC()
	w := &Writer{
		dir:      dir,
		opts:     opts,
		redactor: redactor,
		written:  map[string]bool{},
		now:      opts.Now,
		manifest: &Manifest{
			SchemaVersion:           ManifestSchemaVersion,
			GeneratedAt:             now,
			Source:                  string(opts.Source),
			MetadataSchemaVersion:   schema.MetadataSchemaVersion,
			PushContractVersion:     string(opts.PushContractVersion),
			RedactionLevel:          redactor.Level(),
			RedactionRuleSetVersion: redactor.RuleSetVersion(),
			Database:                filepath.Base(opts.Database),
			Seed:                    opts.Seed,
			SplitTargets:            opts.Targets,
			SplitCounts:             opts.SplitCounts,
		},
	}
	return w, nil
}

// Add writes one local session: a redacted transcript envelope and its
// UnifiedMetadata record.
func (w *Writer) Add(artifact corpus.SessionArtifact, payload []byte) error {
	meta, err := BuildMetadata(artifact, w.now())
	if err != nil {
		return err
	}
	return w.add(meta, payload)
}

// AddVillage writes one collective transcript with metadata reconstructed
// from the pull surface.
func (w *Writer) AddVillage(pull corpus.PulledTranscript, detail *schema.SessionDetailPayload, payload []byte) error {
	meta, err := BuildPullMetadata(pull, detail, w.now())
	if err != nil {
		return err
	}
	if err := w.add(meta, payload); err != nil {
		return err
	}
	w.manifest.VillagePulls++
	return nil
}

func (w *Writer) add(meta *schema.UnifiedMetadata, payload []byte) error {
	if strings.ContainsAny(meta.SessionID.String(), `/\`) {
		return fmt.Errorf("session id %q contains a path separator", meta.SessionID)
	}
	transcriptBytes, err := w.buildTranscript(payload)
	if err != nil {
		return fmt.Errorf("session %s: %w", meta.SessionID, err)
	}
	transcriptPath := filepath.Join("transcripts", meta.SessionID.String()+".jsonl")
	if err := os.WriteFile(filepath.Join(w.dir, transcriptPath), transcriptBytes, 0o644); err != nil {
		return fmt.Errorf("write %s: %w", transcriptPath, err)
	}

	redacted := w.redactor.RedactMetadata(meta)
	contentHash := schema.ComputeTranscriptHash(transcriptBytes)
	redacted.ContentHash = contentHash
	redactedAt := w.now().UnixMilli()
	redacted.Redaction = schema.RedactionInfo{
		Applied:             true,
		Level:               w.redactor.Level(),
		RuleSetVersion:      w.redactor.RuleSetVersion(),
		RedactedAtMs:        &redactedAt,
		ContentHashAtRedact: contentHash,
	}
	redacted.MetadataHash = schema.ComputeMetadataHash(redacted)
	line, err := json.Marshal(redacted)
	if err != nil {
		return fmt.Errorf("encode metadata for session %s: %w", meta.SessionID, err)
	}
	if _, err := w.metadata.Write(append(line, '\n')); err != nil {
		return fmt.Errorf("write metadata for session %s: %w", meta.SessionID, err)
	}
	w.written[meta.SessionID.String()] = true
	w.manifest.Sessions++
	return nil
}

// buildTranscript wraps the session detail payload in a TranscriptContent
// envelope and runs the redaction pipeline over the encoded bytes.
func (w *Writer) buildTranscript(payload []byte) ([]byte, error) {
	var detail schema.SessionDetailPayload
	if err := json.Unmarshal(payload, &detail); err != nil {
		return nil, fmt.Errorf("decode session detail: %w", err)
	}
	detail.SchemaVersion = w.opts.PushContractVersion
	envelope := schema.TranscriptContent{
		ContractVersion: w.opts.PushContractVersion,
		Kind:            schema.ContentKindSessionDetail,
		SessionDetail:   &detail,
	}
	raw, err := json.Marshal(envelope)
	if err != nil {
		return nil, fmt.Errorf("encode transcript envelope: %w", err)
	}
	redacted, err := redact.RedactJSONLBytes(w.redactor, raw)
	if err != nil {
		return nil, fmt.Errorf("redact transcript: %w", err)
	}
	return redacted, nil
}

// SetIndexes writes the pull request and trace indexes. Trace records whose
// session was not written are dropped and counted in the manifest.
func (w *Writer) SetIndexes(prs []PRRecord, traces []TraceRecord) error {
	resolved := traces[:0]
	dumpedByPR := map[string]int{}
	unresolved := 0
	for _, trace := range traces {
		if !w.written[trace.SessionID] {
			unresolved++
			continue
		}
		resolved = append(resolved, trace)
		dumpedByPR[trace.PR]++
	}
	for i := range prs {
		prs[i].DumpedSessions = dumpedByPR[prs[i].ID]
	}
	w.manifest.UnresolvedTraces = unresolved
	sort.Slice(prs, func(i, j int) bool { return prs[i].ID < prs[j].ID })
	sort.Slice(resolved, func(i, j int) bool {
		if resolved[i].PR != resolved[j].PR {
			return resolved[i].PR < resolved[j].PR
		}
		return resolved[i].SessionID < resolved[j].SessionID
	})
	if err := writeJSONL(filepath.Join(w.dir, "pull_requests.jsonl"), prs); err != nil {
		return err
	}
	if err := writeJSONL(filepath.Join(w.dir, "traces.jsonl"), resolved); err != nil {
		return err
	}
	w.manifest.PullRequests = len(prs)
	w.manifest.Traces = len(resolved)
	return nil
}

// SetVillagePulls writes the collective provenance index.
func (w *Writer) SetVillagePulls(pulls []schema.PullTranscriptInfo) error {
	sort.Slice(pulls, func(i, j int) bool { return pulls[i].TranscriptID < pulls[j].TranscriptID })
	return writeJSONL(filepath.Join(w.dir, "village_pulls.jsonl"), pulls)
}

// Fail records a session that could not be written.
func (w *Writer) Fail(sessionID string, err error) {
	message := err.Error()
	if home, homeErr := os.UserHomeDir(); homeErr == nil && home != "" {
		message = strings.ReplaceAll(message, home, "~")
	}
	w.manifest.FailedSessions = append(w.manifest.FailedSessions, FailedSession{SessionID: sessionID, Error: message})
}

// Finish writes the metadata stream atomically and writes the manifest.
func (w *Writer) Finish() (*Manifest, error) {
	metadataPath := filepath.Join(w.dir, "metadata.jsonl")
	tmp := metadataPath + ".tmp"
	if err := os.WriteFile(tmp, w.metadata.Bytes(), 0o644); err != nil {
		return nil, fmt.Errorf("write %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, metadataPath); err != nil {
		return nil, fmt.Errorf("replace %s: %w", metadataPath, err)
	}
	sort.Slice(w.manifest.FailedSessions, func(i, j int) bool {
		return w.manifest.FailedSessions[i].SessionID < w.manifest.FailedSessions[j].SessionID
	})
	if err := writeJSON(filepath.Join(w.dir, "manifest.json"), w.manifest); err != nil {
		return nil, err
	}
	return w.manifest, nil
}

func writeJSONL(path string, records any) error {
	fh, err := os.Create(path)
	if err != nil {
		return fmt.Errorf("create %s: %w", path, err)
	}
	defer fh.Close()
	buffer := bufio.NewWriter(fh)
	switch value := records.(type) {
	case []PRRecord:
		for _, record := range value {
			if err := encodeLine(buffer, record); err != nil {
				return err
			}
		}
	case []TraceRecord:
		for _, record := range value {
			if err := encodeLine(buffer, record); err != nil {
				return err
			}
		}
	case []schema.PullTranscriptInfo:
		for _, record := range value {
			if err := encodeLine(buffer, record); err != nil {
				return err
			}
		}
	default:
		return fmt.Errorf("writeJSONL %s: unsupported record type %T", path, records)
	}
	if err := buffer.Flush(); err != nil {
		return fmt.Errorf("flush %s: %w", path, err)
	}
	return nil
}

func encodeLine(buffer *bufio.Writer, value any) error {
	line, err := json.Marshal(value)
	if err != nil {
		return fmt.Errorf("encode record: %w", err)
	}
	if _, err := buffer.Write(append(line, '\n')); err != nil {
		return fmt.Errorf("write record: %w", err)
	}
	return nil
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

// ErrUnsupportedSource reports an unknown dump source.
var ErrUnsupportedSource = errors.New("dump: unsupported source")
