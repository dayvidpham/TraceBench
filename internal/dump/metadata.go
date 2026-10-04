// Package dump writes a flat, publishable dataset: schema.UnifiedMetadata
// records in metadata.jsonl, redacted schema.TranscriptContent envelopes in
// transcripts/, and separate pull request and trace indexes.
package dump

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"time"

	"github.com/peasant-labs/schema"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

// BuildMetadata converts a recorded session artifact into the canonical
// schema.UnifiedMetadata sidecar. Machine-specific paths (cwd, worktree,
// source and project file paths) are deliberately omitted; the caller runs
// the redaction pipeline as a second layer.
func BuildMetadata(a corpus.SessionArtifact, now time.Time) (*schema.UnifiedMetadata, error) {
	meta := schema.NewUnifiedMetadata()
	sessionID, err := schema.NewSessionID(a.ID)
	if err != nil {
		return nil, fmt.Errorf("session id %q: %w", a.ID, err)
	}
	meta.SessionID = sessionID
	if a.ParentID != "" {
		parent, err := schema.NewSessionID(a.ParentID)
		if err != nil {
			return nil, fmt.Errorf("session %s parent id %q: %w", a.ID, a.ParentID, err)
		}
		meta.ParentUUID = &parent
	}
	meta.ModelHarness = schema.Harness(a.Harness)
	meta.Model = schema.ModelID(a.ModelID)
	meta.Version = a.ToolVersion
	meta.Timestamp = schema.TimestampInfo{Start: a.StartMS, End: a.EndMS}
	if a.IngestedMS > 0 {
		ingested := a.IngestedMS
		meta.Timestamp.Ingested = &ingested
	}

	format := schema.SourceFormat(a.SourceFormat)
	if !format.IsValid() {
		return nil, fmt.Errorf("session %s source format %q is outside the schema set", a.ID, a.SourceFormat)
	}
	meta.Source = schema.SourceInfo{Format: format}

	if a.Branch != "" {
		branch := a.Branch
		meta.Git.Branch = &branch
	}
	if a.ProjectRemote != "" {
		remote := a.ProjectRemote
		meta.Git.Remote = &remote
	}
	if a.GitTracking != "" {
		tracking := a.GitTracking
		meta.Git.Tracking = &tracking
	}
	for _, commit := range a.Commits {
		meta.Git.Commits = append(meta.Git.Commits, schema.CommitInfo{
			Hash:        commit.Hash,
			Message:     commit.Message,
			AuthorName:  commit.AuthorName,
			AuthorEmail: commit.AuthorEmail,
			CommitTime:  commit.CommitTime,
			AuthorTime:  commit.AuthorTime,
		})
	}
	for _, association := range a.Associations {
		id, err := schema.NewAssociationID(association.ID)
		if err != nil {
			return nil, fmt.Errorf("session %s association %q: %w", a.ID, association.ID, err)
		}
		meta.Git.Associations = append(meta.Git.Associations, schema.PublishedAssociation{
			ID:                 id,
			ObservedCommitHash: association.ObservedCommitHash,
		})
	}

	meta.Project = schema.ProjectContext{
		Hash: schema.ProjectHash(a.ProjectHash),
		Name: a.ProjectName,
	}
	meta.HostSlug = pseudonymHost(a.HostSlug)
	meta.Stats = schema.SessionStats{
		TurnCount:            a.TurnCount,
		InputSubmissionCount: a.InputSubmissionCount,
		ToolCallCount:        a.ToolCalls,
		SubagentCount:        a.SubagentCount,
		DurationMs:           durationMS(a),
		TokensIn:             a.InputTokens,
		TokensOut:            a.OutputTokens,
	}
	for _, childID := range a.SubagentIDs {
		child, err := schema.NewSessionID(childID)
		if err != nil {
			return nil, fmt.Errorf("session %s subagent id %q: %w", a.ID, childID, err)
		}
		meta.Subagents = append(meta.Subagents, schema.SubagentRef{SessionID: child, ParentUUID: sessionID})
	}
	if a.RootSessionID != "" {
		root, err := schema.NewSessionID(a.RootSessionID)
		if err != nil {
			return nil, fmt.Errorf("session %s root id %q: %w", a.ID, a.RootSessionID, err)
		}
		meta.RootSessionID = &root
	}
	if a.SessionPurpose != "" {
		purpose := schema.SessionPurpose(a.SessionPurpose)
		if !purpose.IsValid() {
			return nil, fmt.Errorf("session %s purpose %q is outside the schema set", a.ID, a.SessionPurpose)
		}
		meta.Purpose = purpose
	}
	for _, relationship := range a.Relationships {
		converted, err := convertRelationship(a.ID, relationship)
		if err != nil {
			return nil, err
		}
		meta.Relationships = append(meta.Relationships, converted)
	}
	if a.AdapterVersion > 0 {
		adapter := int(a.AdapterVersion)
		meta.AdapterVersion = &adapter
	}
	derived := now.UnixMilli()
	meta.DerivedAt = &derived
	return &meta, nil
}

// BuildPullMetadata reconstructs UnifiedMetadata for a transcript pulled from
// a collective. Fields the pull surface does not carry are omitted rather
// than invented.
func BuildPullMetadata(pull corpus.PulledTranscript, detail *schema.SessionDetailPayload, now time.Time) (*schema.UnifiedMetadata, error) {
	meta := schema.NewUnifiedMetadata()

	localID := pull.LocalSessionID
	if localID == "" {
		localID = pull.TranscriptID
	}
	sessionID, err := schema.NewSessionID(localID)
	if err != nil {
		// The village transcript id is always a UUID; fall back to it so a
		// provider-specific local id shape never blocks the dump.
		sessionID, err = schema.NewSessionID(pull.TranscriptID)
		if err != nil {
			return nil, fmt.Errorf("pull %s session id %q: %w", pull.TranscriptID, localID, err)
		}
	}
	meta.SessionID = sessionID
	if detail != nil {
		if detail.ParentSessionID != nil {
			meta.ParentUUID = detail.ParentSessionID
		}
		meta.Model = schema.ModelID(detail.Model)
		meta.Timestamp = schema.TimestampInfo{
			Start: detail.StartTime.UnixMilli(),
			End:   detail.EndTime.UnixMilli(),
		}
		if detail.GitBranch != "" {
			branch := detail.GitBranch
			meta.Git.Branch = &branch
		}
		if detail.GitRemote != "" {
			remote := detail.GitRemote
			meta.Git.Remote = &remote
		}
		meta.Stats = schema.SessionStats{
			TurnCount:     detail.TurnCount,
			ToolCallCount: detail.ToolCallCount,
			DurationMs:    int64(detail.DurationMins * 60000),
			TokensIn:      detail.TokensIn,
			TokensOut:     detail.TokensOut,
		}
		if detail.Purpose != "" && detail.Purpose.IsValid() {
			meta.Purpose = detail.Purpose
		}
		meta.Relationships = append(meta.Relationships, detail.Relationships...)
	}
	meta.ModelHarness = schema.Harness(pull.Harness)
	meta.Source = schema.SourceInfo{Format: schema.SourceFormat("jsonl")}
	meta.Project = schema.ProjectContext{
		Hash: schema.ProjectHash(pseudonymHash(pull.ProjectName)),
		Name: pull.ProjectName,
	}
	meta.HostSlug = pseudonymHost(pull.VillageHost)
	derived := now.UnixMilli()
	meta.DerivedAt = &derived
	return &meta, nil
}

func convertRelationship(sessionID string, record corpus.RelationshipRecord) (schema.SessionRelationship, error) {
	relationship := schema.SessionRelationship{
		Kind:        schema.SessionRelationshipKind(record.Kind),
		TargetState: schema.RelationshipTargetState(record.TargetState),
		Evidence:    schema.EvidenceKind(record.Evidence),
	}
	if !relationship.Kind.IsValid() || !relationship.TargetState.IsValid() || !relationship.Evidence.IsValid() {
		return relationship, fmt.Errorf("session %s relationship (%s/%s/%s) is outside the schema sets",
			sessionID, record.Kind, record.TargetState, record.Evidence)
	}
	if record.TargetLocalID != "" {
		target, err := schema.NewSessionID(record.TargetLocalID)
		if err != nil {
			return relationship, fmt.Errorf("session %s relationship target %q: %w", sessionID, record.TargetLocalID, err)
		}
		relationship.TargetLocalID = &target
	}
	return relationship, nil
}

func durationMS(a corpus.SessionArtifact) int64 {
	if a.EndMS > a.StartMS {
		return a.EndMS - a.StartMS
	}
	if a.DurationMinutes > 0 {
		return int64(a.DurationMinutes * 60000)
	}
	return 0
}

// pseudonymHost replaces a machine host slug with a stable pseudonym so
// records from the same host stay correlated without exposing the name.
func pseudonymHost(slug string) schema.HostSlug {
	if slug == "" {
		return schema.HostSlug("unknown")
	}
	return schema.HostSlug("host-" + pseudonymHash(slug))
}

func pseudonymHash(value string) string {
	if value == "" {
		return "unknown"
	}
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])[:12]
}
