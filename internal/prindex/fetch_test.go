package prindex

import (
	_ "embed"
	"encoding/json"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

//go:embed testdata/fetch.yaml
var fetchYAML []byte

type fetchFixtures struct {
	Scenarios []fetchScenario `yaml:"scenarios"`
}

type fetchScenario struct {
	Name       string `yaml:"name"`
	Repo       string `yaml:"repo"`
	Payload    string `yaml:"payload"`
	ExpectBody string `yaml:"expect_body"`
	ExpectID   string `yaml:"expect_id"`
}

func loadFetchFixtures(t *testing.T) fetchFixtures {
	t.Helper()
	var fixtures fetchFixtures
	if err := yaml.Unmarshal(fetchYAML, &fixtures); err != nil {
		t.Fatalf("decode fetch fixtures: %v", err)
	}
	if len(fixtures.Scenarios) == 0 {
		t.Fatal("fetch fixtures contain no scenarios")
	}
	names := map[string]bool{}
	seenBody := false
	seenBare := false
	for i, scenario := range fixtures.Scenarios {
		if scenario.Name == "" {
			t.Fatalf("scenario %d has no name", i)
		}
		if names[scenario.Name] {
			t.Fatalf("duplicate scenario name %q", scenario.Name)
		}
		names[scenario.Name] = true
		if scenario.Repo == "" || scenario.Payload == "" || scenario.ExpectID == "" {
			t.Fatalf("scenario %q lacks repo, payload, or expect_id", scenario.Name)
		}
		if scenario.ExpectBody != "" {
			seenBody = true
		}
		if !strings.Contains(scenario.Payload, `"body"`) {
			seenBare = true
		}
	}
	if !seenBody {
		t.Fatal("fetch fixtures cover no scenario with a body")
	}
	if !seenBare {
		t.Fatal("fetch fixtures cover no scenario without a body key")
	}
	return fixtures
}

// TestToPullRequestFixtures verifies the `gh pr list --json` decoding and
// mapping production path carries the body through to the corpus model,
// including payloads written before the body field existed.
func TestToPullRequestFixtures(t *testing.T) {
	for _, scenario := range loadFetchFixtures(t).Scenarios {
		t.Run(scenario.Name, func(t *testing.T) {
			var raw ghPullRequest
			if err := json.Unmarshal([]byte(scenario.Payload), &raw); err != nil {
				t.Fatalf("decode gh payload: %v", err)
			}
			pr := toPullRequest(scenario.Repo, raw)
			if pr.Body != scenario.ExpectBody {
				t.Fatalf("body %q, want %q", pr.Body, scenario.ExpectBody)
			}
			if pr.ID() != scenario.ExpectID {
				t.Fatalf("id %q, want %q", pr.ID(), scenario.ExpectID)
			}
			if pr.Title == "" || pr.URL == "" {
				t.Fatalf("mapping dropped title or url: %+v", pr)
			}
		})
	}
}

//go:embed testdata/issues.yaml
var issuesYAML []byte

type issueFixtures struct {
	Scenarios []issueScenario `yaml:"scenarios"`
}

type issueScenario struct {
	Name   string        `yaml:"name"`
	Issues []ghIssue     `yaml:"issues"`
	PRs    []issuePR     `yaml:"prs"`
	Expect []issueExpect `yaml:"expect"`
	Error  string        `yaml:"error"`
}

type issuePR struct {
	Number  int    `yaml:"number"`
	HeadRef string `yaml:"head_ref"`
}

type issueExpect struct {
	PRNumber    int    `yaml:"pr_number"`
	IssueNumber int    `yaml:"issue_number"`
	IssueBody   string `yaml:"issue_body"`
}

func loadIssueFixtures(t *testing.T) issueFixtures {
	t.Helper()
	var fixtures issueFixtures
	if err := yaml.Unmarshal(issuesYAML, &fixtures); err != nil {
		t.Fatalf("decode issue fixtures: %v", err)
	}
	if len(fixtures.Scenarios) == 0 {
		t.Fatal("issue fixtures contain no scenarios")
	}
	names := map[string]bool{}
	linked, unlinked, closed := false, false, false
	for i, scenario := range fixtures.Scenarios {
		if scenario.Name == "" {
			t.Fatalf("scenario %d has no name", i)
		}
		if names[scenario.Name] {
			t.Fatalf("duplicate scenario name %q", scenario.Name)
		}
		names[scenario.Name] = true
		if len(scenario.PRs) == 0 {
			t.Fatalf("scenario %q has no pull requests", scenario.Name)
		}
		if scenario.Error != "" {
			closed = true
		}
		for _, want := range scenario.Expect {
			if want.IssueNumber == 0 {
				unlinked = true
			} else {
				linked = true
			}
		}
	}
	if !linked || !unlinked || !closed {
		t.Fatal("issue fixtures must cover a linked, an unlinked, and a failing scenario")
	}
	return fixtures
}

// TestAttachIssuesFixtures verifies the production linking rule: a head
// branch like "peasant-337--..." attaches issue 337, branches without an
// issue number stay unlinked, and a named issue the fetch missed fails
// closed instead of silently dropping context.
func TestAttachIssuesFixtures(t *testing.T) {
	for _, scenario := range loadIssueFixtures(t).Scenarios {
		t.Run(scenario.Name, func(t *testing.T) {
			issues := make(map[int]corpus.LinkedIssue, len(scenario.Issues))
			for _, raw := range scenario.Issues {
				issues[raw.Number] = linkedIssue(raw)
			}
			prs := make([]corpus.PullRequest, 0, len(scenario.PRs))
			for _, pr := range scenario.PRs {
				prs = append(prs, corpus.PullRequest{Number: pr.Number, HeadRef: pr.HeadRef})
			}
			err := attachIssues(prs, issues)
			if scenario.Error != "" {
				if err == nil || !strings.Contains(err.Error(), scenario.Error) {
					t.Fatalf("attachIssues error %v, want substring %q", err, scenario.Error)
				}
				return
			}
			if err != nil {
				t.Fatalf("attachIssues: %v", err)
			}
			byNumber := make(map[int]corpus.PullRequest, len(prs))
			for _, pr := range prs {
				byNumber[pr.Number] = pr
			}
			for _, want := range scenario.Expect {
				pr, ok := byNumber[want.PRNumber]
				if !ok {
					t.Fatalf("pull request %d not found", want.PRNumber)
				}
				if want.IssueNumber == 0 {
					if pr.Issue != nil {
						t.Fatalf("pull request %d linked to %+v, want unlinked", want.PRNumber, pr.Issue)
					}
					continue
				}
				if pr.Issue == nil {
					t.Fatalf("pull request %d is unlinked, want issue %d", want.PRNumber, want.IssueNumber)
				}
				if pr.Issue.Number != want.IssueNumber || pr.Issue.Body != want.IssueBody {
					t.Fatalf("pull request %d issue %+v, want number %d body %q",
						want.PRNumber, *pr.Issue, want.IssueNumber, want.IssueBody)
				}
			}
		})
	}
}
