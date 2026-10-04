package dump

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/peasant-labs/schema"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

// sessionWarningPattern matches the per-session warnings the peasant export
// command writes to stderr while continuing past failures.
var sessionWarningPattern = regexp.MustCompile(`(?m)^warning: session ([^:]+): (.*)$`)

// PeasantExport acquires SessionDetailPayload JSON through the peasant CLI,
// which owns the canonical entries-to-turns projection.
type PeasantExport struct {
	Bin     string
	DataDir string
	WorkDir string
}

// Payloads exports the given sessions and returns payload bytes keyed by
// session id. Sessions the CLI could not export appear in the failures map.
func (p *PeasantExport) Payloads(ctx context.Context, ids []string) (map[string][]byte, map[string]string, error) {
	if p.Bin == "" {
		return nil, nil, fmt.Errorf("peasant binary is not configured")
	}
	if p.DataDir == "" {
		return nil, nil, fmt.Errorf("peasant data directory is not configured")
	}
	if err := os.MkdirAll(p.WorkDir, 0o755); err != nil {
		return nil, nil, fmt.Errorf("create export work directory %s: %w", p.WorkDir, err)
	}
	idsPath := filepath.Join(p.WorkDir, "session-ids.txt")
	if err := os.WriteFile(idsPath, []byte(strings.Join(ids, "\n")+"\n"), 0o644); err != nil {
		return nil, nil, fmt.Errorf("write session id list %s: %w", idsPath, err)
	}
	outputDir, err := os.MkdirTemp(p.WorkDir, "export-")
	if err != nil {
		return nil, nil, fmt.Errorf("create export output directory: %w", err)
	}

	cmd := exec.CommandContext(ctx, p.Bin, "export", "sessions",
		"--session-from-file", idsPath,
		"--output-dir", outputDir,
		"--data-dir", p.DataDir,
	)
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return nil, nil, fmt.Errorf("peasant export sessions: %w: %s",
			err, strings.TrimSpace(stderr.String()))
	}

	payloads := map[string][]byte{}
	failures := parseSessionWarnings(stderr.String())
	for _, id := range ids {
		path := filepath.Join(outputDir, id+".json")
		data, err := os.ReadFile(path)
		if err != nil {
			if _, ok := failures[id]; !ok {
				failures[id] = "export produced no payload"
			}
			continue
		}
		payloads[id] = data
	}
	return payloads, failures, nil
}

// parseSessionWarnings extracts per-session failure reasons from the export
// command's stderr stream.
func parseSessionWarnings(stderr string) map[string]string {
	failures := map[string]string{}
	for _, match := range sessionWarningPattern.FindAllStringSubmatch(stderr, -1) {
		failures[match[1]] = match[2]
	}
	return failures
}

// ReadPullBundle reads one pulled transcript directory and returns its
// provenance record, session detail, and payload bytes.
func ReadPullBundle(pull corpus.PulledTranscript) (schema.PullTranscriptInfo, *schema.SessionDetailPayload, []byte, error) {
	var info schema.PullTranscriptInfo
	metadataPath := filepath.Join(pull.PullDir, "metadata.json")
	if data, err := os.ReadFile(metadataPath); err == nil {
		if err := json.Unmarshal(data, &info); err != nil {
			return info, nil, nil, fmt.Errorf("decode %s: %w", metadataPath, err)
		}
	} else if !os.IsNotExist(err) {
		return info, nil, nil, fmt.Errorf("read %s: %w", metadataPath, err)
	}

	transcriptPath := filepath.Join(pull.PullDir, "transcript.jsonl")
	data, err := os.ReadFile(transcriptPath)
	if err != nil {
		return info, nil, nil, fmt.Errorf("read %s: %w", transcriptPath, err)
	}
	var envelope schema.TranscriptContent
	if err := json.Unmarshal(firstLine(data), &envelope); err != nil {
		return info, nil, nil, fmt.Errorf("decode %s: %w", transcriptPath, err)
	}
	if envelope.SessionDetail == nil {
		return info, nil, nil, fmt.Errorf("transcript %s carries no session detail", transcriptPath)
	}

	if info.TranscriptID == "" {
		transcriptID, err := schema.NewTranscriptID(pull.TranscriptID)
		if err != nil {
			return info, nil, nil, err
		}
		info.TranscriptID = transcriptID
	}
	if info.LocalID == "" {
		info.LocalID = pull.LocalSessionID
	}
	if info.OwnerUsername == "" {
		info.OwnerUsername = pull.OwnerUsername
	}
	if info.OwnerUserID == "" {
		info.OwnerUserID = pull.OwnerUserID
	}
	if info.ProjectName == "" {
		info.ProjectName = pull.ProjectName
	}
	if info.Harness == "" {
		info.Harness = schema.Harness(pull.Harness)
	}
	if info.License == "" {
		info.License = schema.License(pull.LicenseID)
	}
	if info.ContentHash == "" {
		info.ContentHash = pull.ContentHash
	}
	if info.AnnotationCount == 0 {
		info.AnnotationCount = pull.AnnotationCount
	}
	if info.ContractVersion == "" {
		info.ContractVersion = envelope.ContractVersion
	}

	payload, err := json.Marshal(envelope.SessionDetail)
	if err != nil {
		return info, nil, nil, fmt.Errorf("encode session detail for %s: %w", pull.TranscriptID, err)
	}
	return info, envelope.SessionDetail, payload, nil
}

func firstLine(data []byte) []byte {
	if idx := strings.IndexByte(string(data), '\n'); idx >= 0 {
		return data[:idx]
	}
	return data
}
