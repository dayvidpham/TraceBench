package prindex

import (
	_ "embed"
	"encoding/json"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
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
