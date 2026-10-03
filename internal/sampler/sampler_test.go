package sampler

import (
	_ "embed"
	"reflect"
	"sort"
	"testing"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

//go:embed testdata/selection.yaml
var selectionYAML []byte

type selectionFixtures struct {
	Scenarios []selectionScenario `yaml:"scenarios"`
}

type selectionScenario struct {
	Name       string               `yaml:"name"`
	Config     selectionConfig      `yaml:"config"`
	Candidates []selectionCandidate `yaml:"candidates"`
	Expect     selectionExpect      `yaml:"expect"`
}

type selectionConfig struct {
	Train      int   `yaml:"train"`
	Val        int   `yaml:"val"`
	Test       int   `yaml:"test"`
	Seed       int64 `yaml:"seed"`
	KeepGroups bool  `yaml:"keep_groups"`
}

type selectionCandidate struct {
	ID       string `yaml:"id"`
	Group    string `yaml:"group"`
	MergedAt string `yaml:"merged_at"`
	Lines    int    `yaml:"lines"`
}

type selectionExpect struct {
	Selected           int    `yaml:"selected"`
	Counts             *count `yaml:"counts"`
	CoverTimeQuartiles bool   `yaml:"cover_time_quartiles"`
	CoverSizeTertiles  bool   `yaml:"cover_size_tertiles"`
	Deterministic      bool   `yaml:"deterministic"`
}

type count struct {
	Train int `yaml:"train"`
	Val   int `yaml:"val"`
	Test  int `yaml:"test"`
}

func loadSelectionFixtures(t *testing.T) selectionFixtures {
	t.Helper()
	var fixtures selectionFixtures
	if err := yaml.Unmarshal(selectionYAML, &fixtures); err != nil {
		t.Fatalf("decode selection fixtures: %v", err)
	}
	if len(fixtures.Scenarios) == 0 {
		t.Fatal("selection fixtures contain no scenarios")
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
		if len(scenario.Candidates) == 0 {
			t.Fatalf("scenario %q has no candidates", scenario.Name)
		}
	}
	return fixtures
}

func TestSelectFixtures(t *testing.T) {
	fixtures := loadSelectionFixtures(t)
	if len(fixtures.Scenarios) < 3 {
		t.Fatalf("selection fixtures cover only %d scenarios", len(fixtures.Scenarios))
	}
	for _, scenario := range fixtures.Scenarios {
		t.Run(scenario.Name, func(t *testing.T) {
			candidates := make([]Candidate, 0, len(scenario.Candidates))
			lines := map[string]int{}
			for _, c := range scenario.Candidates {
				pr := corpus.PullRequest{
					Repo:      corpus.RepoSlug("owner/repo"),
					Number:    len(candidates) + 1,
					MergedAt:  parseSelectionTime(t, c.MergedAt),
					Additions: c.Lines,
				}
				candidates = append(candidates, Candidate{PR: pr, SessionCount: 1, Group: c.Group})
				lines[pr.ID()] = c.Lines
			}
			config := Config{
				Train: scenario.Config.Train, Val: scenario.Config.Val, Test: scenario.Config.Test,
				Seed: scenario.Config.Seed, KeepGroups: scenario.Config.KeepGroups,
			}
			result, err := Select(candidates, config)
			if err != nil {
				t.Fatalf("Select: %v", err)
			}
			if result.Selected != scenario.Expect.Selected {
				t.Fatalf("selected %d, want %d", result.Selected, scenario.Expect.Selected)
			}
			if len(result.Assignments) != scenario.Expect.Selected {
				t.Fatalf("assignments %d, want %d", len(result.Assignments), scenario.Expect.Selected)
			}
			if scenario.Expect.Counts != nil {
				want := map[Split]int{Train: scenario.Expect.Counts.Train, Val: scenario.Expect.Counts.Val, Test: scenario.Expect.Counts.Test}
				for split, size := range want {
					if result.Counts[split] != size {
						t.Fatalf("split %s has %d pull requests, want %d (all counts: %v)", split, result.Counts[split], size, result.Counts)
					}
				}
			}

			seen := map[string]Split{}
			groupSplit := map[string]Split{}
			for _, assignment := range result.Assignments {
				if _, dup := seen[assignment.PR.ID()]; dup {
					t.Fatalf("pull request %s assigned twice", assignment.PR.ID())
				}
				seen[assignment.PR.ID()] = assignment.Split
				if assignment.Group != "" {
					if split, ok := groupSplit[assignment.Group]; ok && split != assignment.Split {
						t.Fatalf("group %s spans splits %s and %s", assignment.Group, split, assignment.Split)
					}
					groupSplit[assignment.Group] = assignment.Split
				}
			}

			if scenario.Expect.CoverTimeQuartiles {
				assertBinCoverage(t, "time quartiles", candidates, seen, 4, func(c Candidate) float64 {
					return float64(c.PR.MergedAt.UnixNano())
				})
			}
			if scenario.Expect.CoverSizeTertiles {
				assertBinCoverage(t, "size tertiles", candidates, seen, 3, func(c Candidate) float64 {
					return float64(c.PR.LinesChanged())
				})
			}
			if scenario.Expect.Deterministic {
				again, err := Select(candidates, config)
				if err != nil {
					t.Fatalf("Select (second run): %v", err)
				}
				if !reflect.DeepEqual(result.Assignments, again.Assignments) {
					t.Fatal("selection is not deterministic for the same seed")
				}
			}
		})
	}
}

// assertBinCoverage checks that every equal-count bin of the pool ordering
// contains at least one selected candidate.
func assertBinCoverage(t *testing.T, axis string, candidates []Candidate, selected map[string]Split, bins int, value func(Candidate) float64) {
	t.Helper()
	ordered := append([]Candidate(nil), candidates...)
	sort.Slice(ordered, func(i, j int) bool {
		vi, vj := value(ordered[i]), value(ordered[j])
		if vi != vj {
			return vi < vj
		}
		return ordered[i].PR.ID() < ordered[j].PR.ID()
	})
	covered := map[int]bool{}
	for i, c := range ordered {
		if _, ok := selected[c.PR.ID()]; ok {
			covered[i*bins/len(ordered)] = true
		}
	}
	for bin := 0; bin < bins; bin++ {
		if !covered[bin] {
			t.Fatalf("%s bin %d has no selected candidate", axis, bin)
		}
	}
}

func parseSelectionTime(t *testing.T, value string) time.Time {
	t.Helper()
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil {
		t.Fatalf("parse fixture time %q: %v", value, err)
	}
	return parsed.UTC()
}
