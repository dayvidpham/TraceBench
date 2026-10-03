package prindex

import (
	_ "embed"
	"fmt"
	"reflect"
	"sort"
	"testing"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

//go:embed testdata/attribution.yaml
var attributionYAML []byte

type attributionFixtures struct {
	Scenarios []attributionScenario `yaml:"scenarios"`
}

type attributionScenario struct {
	Name            string               `yaml:"name"`
	RepoHost        string               `yaml:"repo_host"`
	IgnoredBranches []string             `yaml:"ignored_branches"`
	PRs             []attributionPR      `yaml:"prs"`
	Sessions        []attributionSession `yaml:"sessions"`
	CommitPRs       map[string][]string  `yaml:"commit_prs"`
	Expect          []attributionLink    `yaml:"expect"`
}

type attributionPR struct {
	Repo      string `yaml:"repo"`
	Number    int    `yaml:"number"`
	HeadRef   string `yaml:"head_ref"`
	CreatedAt string `yaml:"created_at"`
	MergedAt  string `yaml:"merged_at"`
}

type attributionSession struct {
	ID         string   `yaml:"id"`
	ParentID   string   `yaml:"parent_id"`
	Worktree   string   `yaml:"worktree"`
	Branch     string   `yaml:"branch"`
	SessionCwd string   `yaml:"session_cwd"`
	ProjectCwd string   `yaml:"project_cwd"`
	SourcePath string   `yaml:"source_path"`
	Commits    []string `yaml:"commits"`
	StartedAt  string   `yaml:"started_at"`
	EndedAt    string   `yaml:"ended_at"`
}

type attributionLink struct {
	PR      string `yaml:"pr"`
	Session string `yaml:"session"`
	Method  string `yaml:"method"`
}

func loadAttributionFixtures(t *testing.T) attributionFixtures {
	t.Helper()
	var fixtures attributionFixtures
	if err := yaml.Unmarshal(attributionYAML, &fixtures); err != nil {
		t.Fatalf("decode attribution fixtures: %v", err)
	}
	if len(fixtures.Scenarios) == 0 {
		t.Fatal("attribution fixtures contain no scenarios")
	}
	names := map[string]bool{}
	for i, scenario := range fixtures.Scenarios {
		if scenario.Name == "" {
			t.Fatalf("scenario %d has no name", i)
		}
		if names[scenario.Name] {
			t.Fatalf("duplicate scenario name %q", scenario.Name)
		}
		names[scenario.Name] = true
		if len(scenario.Sessions) == 0 {
			t.Fatalf("scenario %q has no sessions", scenario.Name)
		}
	}
	return fixtures
}

func TestAttributeFixtures(t *testing.T) {
	fixtures := loadAttributionFixtures(t)
	if len(fixtures.Scenarios) < 10 {
		t.Fatalf("attribution fixtures cover only %d scenarios", len(fixtures.Scenarios))
	}
	for _, scenario := range fixtures.Scenarios {
		t.Run(scenario.Name, func(t *testing.T) {
			sessions := make([]corpus.Session, 0, len(scenario.Sessions))
			sessionCommits := map[string][]string{}
			for _, s := range scenario.Sessions {
				sessions = append(sessions, corpus.Session{
					ID:         s.ID,
					ParentID:   s.ParentID,
					Worktree:   s.Worktree,
					Branch:     s.Branch,
					SessionCwd: s.SessionCwd,
					ProjectCwd: s.ProjectCwd,
					SourcePath: s.SourcePath,
					StartMS:    parseFixtureTime(t, s.StartedAt).UnixMilli(),
					EndMS:      parseFixtureTime(t, s.EndedAt).UnixMilli(),
				})
				if len(s.Commits) > 0 {
					sessionCommits[s.ID] = s.Commits
				}
			}
			prs := make([]corpus.PullRequest, 0, len(scenario.PRs))
			for _, p := range scenario.PRs {
				prs = append(prs, corpus.PullRequest{
					Repo:      corpus.RepoSlug(p.Repo),
					Number:    p.Number,
					HeadRef:   p.HeadRef,
					CreatedAt: parseFixtureTime(t, p.CreatedAt),
					MergedAt:  parseFixtureTime(t, p.MergedAt),
				})
			}

			extractor := NewKeyExtractor(scenario.RepoHost, scenario.IgnoredBranches)
			links := Attribute(sessions, prs, extractor, LinkInput{
				SessionCommits: sessionCommits,
				CommitPRs:      scenario.CommitPRs,
			})

			got := linkSet(links)
			want := map[string]string{}
			for _, e := range scenario.Expect {
				key := e.PR + "|" + e.Session
				if _, dup := want[key]; dup {
					t.Fatalf("fixture declares duplicate expectation %s", key)
				}
				want[key] = e.Method
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("attribution mismatch\n got: %v\nwant: %v", sortedKeys(got), sortedKeys(want))
			}
		})
	}
}

func linkSet(links []corpus.TraceLink) map[string]string {
	out := map[string]string{}
	for _, link := range links {
		out[link.PRID+"|"+link.SessionID] = string(link.Method)
	}
	return out
}

func sortedKeys(m map[string]string) []string {
	keys := make([]string, 0, len(m))
	for key := range m {
		keys = append(keys, fmt.Sprintf("%s=%s", key, m[key]))
	}
	sort.Strings(keys)
	return keys
}

func parseFixtureTime(t *testing.T, value string) time.Time {
	t.Helper()
	if value == "" {
		t.Fatalf("fixture time value is empty")
	}
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil {
		t.Fatalf("parse fixture time %q: %v", value, err)
	}
	return parsed.UTC()
}
