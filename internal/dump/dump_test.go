package dump

import (
	"bufio"
	_ "embed"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/peasant-labs/redact"
	"github.com/peasant-labs/schema"
	"gopkg.in/yaml.v3"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

//go:embed testdata/metadata.yaml
var metadataYAML []byte

type metadataFixtures struct {
	Scenarios []metadataScenario `yaml:"scenarios"`
}

type metadataScenario struct {
	Name          string                 `yaml:"name"`
	Session       metadataSession        `yaml:"session"`
	Metrics       metadataMetrics        `yaml:"metrics"`
	Commits       []metadataCommit       `yaml:"commits"`
	Associations  []metadataAssociation  `yaml:"associations"`
	Relationships []metadataRelationship `yaml:"relationships"`
	SubagentIDs   []string               `yaml:"subagent_ids"`
	Expect        metadataExpect         `yaml:"expect"`
	ExpectError   bool                   `yaml:"expect_error"`
}

type metadataSession struct {
	ID             string `yaml:"id"`
	ParentID       string `yaml:"parent_id"`
	Harness        string `yaml:"harness"`
	ModelID        string `yaml:"model_id"`
	StartMS        int64  `yaml:"start_ms"`
	EndMS          int64  `yaml:"end_ms"`
	IngestedMS     int64  `yaml:"ingested_ms"`
	SourceFormat   string `yaml:"source_format"`
	Branch         string `yaml:"branch"`
	Worktree       string `yaml:"worktree"`
	SourcePath     string `yaml:"source_path"`
	ProjectHash    string `yaml:"project_hash"`
	ProjectName    string `yaml:"project_name"`
	ProjectRemote  string `yaml:"project_remote"`
	HostSlug       string `yaml:"host_slug"`
	ToolVersion    string `yaml:"tool_version"`
	RootSessionID  string `yaml:"root_session_id"`
	SessionPurpose string `yaml:"session_purpose"`
	AdapterVersion int64  `yaml:"adapter_version"`
}

type metadataMetrics struct {
	TurnCount       int     `yaml:"turn_count"`
	ToolCalls       int     `yaml:"tool_calls"`
	SubagentCount   int     `yaml:"subagent_count"`
	InputTokens     int     `yaml:"input_tokens"`
	OutputTokens    int     `yaml:"output_tokens"`
	DurationMinutes float64 `yaml:"duration_minutes"`
}

type metadataCommit struct {
	Hash        string `yaml:"hash"`
	Message     string `yaml:"message"`
	AuthorName  string `yaml:"author_name"`
	AuthorEmail string `yaml:"author_email"`
	CommitTime  int64  `yaml:"commit_time"`
	AuthorTime  int64  `yaml:"author_time"`
}

type metadataAssociation struct {
	ID                 string `yaml:"id"`
	ObservedCommitHash string `yaml:"observed_commit_hash"`
}

type metadataRelationship struct {
	Kind          string `yaml:"kind"`
	TargetState   string `yaml:"target_state"`
	TargetLocalID string `yaml:"target_local_id"`
	Evidence      string `yaml:"evidence"`
}

type metadataExpect struct {
	SessionID      string `yaml:"session_id"`
	ParentUUID     string `yaml:"parent_uuid"`
	HostSlugPrefix string `yaml:"host_slug_prefix"`
	Commits        int    `yaml:"commits"`
	Associations   int    `yaml:"associations"`
	Relationships  int    `yaml:"relationships"`
	Subagents      int    `yaml:"subagents"`
	AdapterVersion int    `yaml:"adapter_version"`
	Purpose        string `yaml:"purpose"`
	DurationMS     int64  `yaml:"duration_ms"`
}

func loadMetadataFixtures(t *testing.T) metadataFixtures {
	t.Helper()
	var fixtures metadataFixtures
	if err := yaml.Unmarshal(metadataYAML, &fixtures); err != nil {
		t.Fatalf("decode metadata fixtures: %v", err)
	}
	if len(fixtures.Scenarios) == 0 {
		t.Fatal("metadata fixtures contain no scenarios")
	}
	return fixtures
}

func TestBuildMetadataFixtures(t *testing.T) {
	for _, scenario := range loadMetadataFixtures(t).Scenarios {
		t.Run(scenario.Name, func(t *testing.T) {
			artifact := corpus.SessionArtifact{
				Session: corpus.Session{
					ID: scenario.Session.ID, ParentID: scenario.Session.ParentID,
					Harness: scenario.Session.Harness, ModelID: scenario.Session.ModelID,
					StartMS: scenario.Session.StartMS, EndMS: scenario.Session.EndMS,
					Branch: scenario.Session.Branch, Worktree: scenario.Session.Worktree,
					SourcePath: scenario.Session.SourcePath,
				},
				IngestedMS: scenario.Session.IngestedMS, SourceFormat: scenario.Session.SourceFormat,
				ProjectHash: scenario.Session.ProjectHash, ProjectName: scenario.Session.ProjectName,
				ProjectRemote: scenario.Session.ProjectRemote, HostSlug: scenario.Session.HostSlug,
				ToolVersion: scenario.Session.ToolVersion, RootSessionID: scenario.Session.RootSessionID,
				SessionPurpose: scenario.Session.SessionPurpose, AdapterVersion: scenario.Session.AdapterVersion,
				TurnCount: scenario.Metrics.TurnCount, ToolCalls: scenario.Metrics.ToolCalls,
				SubagentCount: scenario.Metrics.SubagentCount, InputTokens: scenario.Metrics.InputTokens,
				OutputTokens: scenario.Metrics.OutputTokens, DurationMinutes: scenario.Metrics.DurationMinutes,
				SubagentIDs: scenario.SubagentIDs,
			}
			for _, commit := range scenario.Commits {
				artifact.Commits = append(artifact.Commits, corpus.CommitRecord{
					Hash: commit.Hash, Message: commit.Message, AuthorName: commit.AuthorName,
					AuthorEmail: commit.AuthorEmail, CommitTime: commit.CommitTime, AuthorTime: commit.AuthorTime,
				})
			}
			for _, association := range scenario.Associations {
				artifact.Associations = append(artifact.Associations, corpus.AssociationRecord{
					ID: association.ID, ObservedCommitHash: association.ObservedCommitHash,
				})
			}
			for _, relationship := range scenario.Relationships {
				artifact.Relationships = append(artifact.Relationships, corpus.RelationshipRecord{
					Kind: relationship.Kind, TargetState: relationship.TargetState,
					TargetLocalID: relationship.TargetLocalID, Evidence: relationship.Evidence,
				})
			}

			meta, err := BuildMetadata(artifact, time.UnixMilli(123456))
			if scenario.ExpectError {
				if err == nil {
					t.Fatal("expected an error")
				}
				return
			}
			if err != nil {
				t.Fatalf("BuildMetadata: %v", err)
			}
			if string(meta.SessionID) != scenario.Expect.SessionID {
				t.Fatalf("session id %q", meta.SessionID)
			}
			if meta.ParentUUID == nil || string(*meta.ParentUUID) != scenario.Expect.ParentUUID {
				t.Fatalf("parent uuid %v", meta.ParentUUID)
			}
			if !strings.HasPrefix(string(meta.HostSlug), scenario.Expect.HostSlugPrefix) {
				t.Fatalf("host slug %q lacks prefix %q", meta.HostSlug, scenario.Expect.HostSlugPrefix)
			}
			if len(meta.Git.Commits) != scenario.Expect.Commits ||
				len(meta.Git.Associations) != scenario.Expect.Associations ||
				len(meta.Relationships) != scenario.Expect.Relationships ||
				len(meta.Subagents) != scenario.Expect.Subagents {
				t.Fatalf("counts: commits=%d associations=%d relationships=%d subagents=%d",
					len(meta.Git.Commits), len(meta.Git.Associations), len(meta.Relationships), len(meta.Subagents))
			}
			if meta.AdapterVersion == nil || *meta.AdapterVersion != scenario.Expect.AdapterVersion {
				t.Fatalf("adapter version %v", meta.AdapterVersion)
			}
			if string(meta.Purpose) != scenario.Expect.Purpose {
				t.Fatalf("purpose %q", meta.Purpose)
			}
			if meta.Stats.DurationMs != scenario.Expect.DurationMS {
				t.Fatalf("duration %d", meta.Stats.DurationMs)
			}
			// Machine-scoped fields stay out of the record.
			if meta.CWD != "" || meta.Source.FilePath != "" || meta.Project.FilePath != "" || meta.Git.Worktree != nil {
				t.Fatalf("metadata carries machine paths: cwd=%q source=%q project=%q worktree=%v",
					meta.CWD, meta.Source.FilePath, meta.Project.FilePath, meta.Git.Worktree)
			}
		})
	}
}

func TestWriterRedactsAndWritesDump(t *testing.T) {
	dir := t.TempDir()
	writer, err := NewWriter(dir, Options{
		Source:              SourceLocal,
		Database:            "/home/someone/.local/share/peasant/peasant.db",
		PushContractVersion: schema.PushContractVersion("0.1.1"),
		RedactionLevel:      redact.Standard,
	})
	if err != nil {
		t.Fatal(err)
	}

	secret := "AKIAIOSFODNN7EXAMPLE"
	payload, err := json.Marshal(schema.SessionDetailPayload{
		ID:        "11111111-1111-1111-1111-111111111111",
		Harness:   "claude-code",
		StartTime: time.UnixMilli(1000).UTC(),
		EndTime:   time.UnixMilli(2000).UTC(),
		TurnCount: 1,
		Turns: []schema.TurnDetail{{
			Index: 0, Role: "user",
			Content: "deploy with " + secret + " from /home/someone/private",
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	artifact := corpus.SessionArtifact{
		Session: corpus.Session{
			ID: "11111111-1111-1111-1111-111111111111", Harness: "claude-code",
			ModelID: "sonnet", StartMS: 1000, EndMS: 2000,
			Worktree: "/home/someone/repo/worktree", SourcePath: "/home/someone/.claude/x.jsonl",
		},
		SourceFormat: "jsonl", ProjectHash: "abc", ProjectName: "peasant",
		HostSlug: "someone-laptop", TurnCount: 1,
	}
	if err := writer.Add(artifact, payload); err != nil {
		t.Fatal(err)
	}
	if err := writer.SetIndexes([]PRRecord{{ID: "peasant-labs/peasant#1", Split: "train"}},
		[]TraceRecord{
			{PR: "peasant-labs/peasant#1", SessionID: artifact.ID, Method: "exact", Relation: "linked"},
			{PR: "peasant-labs/peasant#1", SessionID: "99999999-9999-9999-9999-999999999999", Method: "exact"},
		}); err != nil {
		t.Fatal(err)
	}
	manifest, err := writer.Finish()
	if err != nil {
		t.Fatal(err)
	}
	if manifest.Sessions != 1 || manifest.PullRequests != 1 || manifest.Traces != 1 || manifest.UnresolvedTraces != 1 {
		t.Fatalf("manifest %+v", manifest)
	}
	prBytes, err := os.ReadFile(filepath.Join(dir, "pull_requests.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var prRecord PRRecord
	if err := json.Unmarshal(prBytes, &prRecord); err != nil {
		t.Fatal(err)
	}
	if prRecord.DumpedSessions != 1 {
		t.Fatalf("dumped sessions %d", prRecord.DumpedSessions)
	}
	if manifest.RedactionLevel != string(redact.Standard) || manifest.RedactionRuleSetVersion == "" {
		t.Fatalf("manifest redaction %+v", manifest)
	}
	if manifest.Database != "peasant.db" {
		t.Fatalf("manifest database %q", manifest.Database)
	}

	transcriptBytes, err := os.ReadFile(filepath.Join(dir, "transcripts", artifact.ID+".jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(transcriptBytes), secret) {
		t.Fatalf("transcript still carries the secret: %s", transcriptBytes)
	}
	var envelope schema.TranscriptContent
	if err := json.Unmarshal(transcriptBytes, &envelope); err != nil {
		t.Fatalf("decode transcript envelope: %v", err)
	}
	if envelope.Kind != schema.ContentKindSessionDetail || envelope.SessionDetail == nil {
		t.Fatalf("envelope %+v", envelope)
	}
	if envelope.ContractVersion != "0.1.1" || envelope.SessionDetail.SchemaVersion != "0.1.1" {
		t.Fatalf("contract versions: %q / %q", envelope.ContractVersion, envelope.SessionDetail.SchemaVersion)
	}

	metadataFH, err := os.Open(filepath.Join(dir, "metadata.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer metadataFH.Close()
	scanner := bufio.NewScanner(metadataFH)
	if !scanner.Scan() {
		t.Fatal("metadata.jsonl is empty")
	}
	var meta schema.UnifiedMetadata
	if err := json.Unmarshal(scanner.Bytes(), &meta); err != nil {
		t.Fatalf("decode metadata record: %v", err)
	}
	if meta.ContentHash == "" || meta.MetadataHash == "" {
		t.Fatalf("missing hashes: %+v", meta)
	}
	if !meta.Redaction.Applied || meta.Redaction.RuleSetVersion == "" {
		t.Fatalf("redaction info %+v", meta.Redaction)
	}
	if meta.CWD != "" || meta.Source.FilePath != "" || meta.Git.Worktree != nil {
		t.Fatalf("metadata carries machine paths: %+v", meta)
	}
	if !strings.HasPrefix(string(meta.HostSlug), "host-") {
		t.Fatalf("host slug %q", meta.HostSlug)
	}
	if meta.SessionID != schema.SessionID(artifact.ID) {
		t.Fatalf("session id %q", meta.SessionID)
	}
}

func TestParseSessionWarnings(t *testing.T) {
	stderr := `warning: session aaa: export session aaa: store: incomplete capture
warning: session bbb: export session bbb: unknown failure code "x"
exported ccc -> /tmp/ccc.json (10 turns)
`
	failures := parseSessionWarnings(stderr)
	if len(failures) != 2 {
		t.Fatalf("failures %v", failures)
	}
	if failures["aaa"] != "export session aaa: store: incomplete capture" {
		t.Fatalf("aaa message %q", failures["aaa"])
	}
	if failures["bbb"] != `export session bbb: unknown failure code "x"` {
		t.Fatalf("bbb message %q", failures["bbb"])
	}
}
