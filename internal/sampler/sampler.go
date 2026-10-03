// Package sampler selects a time- and size-stratified subset of traced pull
// requests and assigns each selection to a train, val, or test split.
package sampler

import (
	"errors"
	"math"
	"math/rand"
	"sort"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

// Split names a dataset partition.
type Split string

const (
	// Train is the training split.
	Train Split = "train"
	// Val is the validation split.
	Val Split = "val"
	// Test is the held-out test split.
	Test Split = "test"
)

// Candidate is a traced pull request eligible for sampling.
type Candidate struct {
	PR           corpus.PullRequest
	SessionCount int
	// Group keeps related pull requests, such as several pull requests for
	// one issue, inside a single split.
	Group string
}

// Assignment is a sampled pull request with its split.
type Assignment struct {
	PR           corpus.PullRequest
	SessionCount int
	Group        string
	Split        Split
}

// Config controls selection and split assignment.
type Config struct {
	Train, Val, Test int
	Seed             int64
	KeepGroups       bool
}

// Result reports what the sampler produced.
type Result struct {
	Assignments []Assignment
	Pool        int
	Selected    int
	Targets     map[Split]int
	Counts      map[Split]int
}

const (
	timeBinCount = 4
	sizeBinCount = 3
)

var splitOrder = []Split{Train, Val, Test}

// Select chooses candidates for the requested split sizes. When more
// candidates exist than the total target, the selection round-robins across
// time and size strata so both axes stay covered; otherwise every candidate
// is selected.
func Select(candidates []Candidate, cfg Config) (Result, error) {
	total := cfg.Train + cfg.Val + cfg.Test
	if cfg.Train < 0 || cfg.Val < 0 || cfg.Test < 0 {
		return Result{}, errors.New("sampler: split sizes must not be negative")
	}
	if total <= 0 {
		return Result{}, errors.New("sampler: split sizes must sum to a positive total")
	}
	targets := map[Split]int{Train: cfg.Train, Val: cfg.Val, Test: cfg.Test}

	selected := candidates
	if len(candidates) > total {
		selected = stratifiedSelect(candidates, total, cfg.Seed)
	}
	assignments, counts := assign(selected, cfg)

	sorted := append([]Assignment(nil), assignments...)
	sort.Slice(sorted, func(i, j int) bool {
		if sorted[i].Split != sorted[j].Split {
			return splitRank(sorted[i].Split) < splitRank(sorted[j].Split)
		}
		if !sorted[i].PR.MergedAt.Equal(sorted[j].PR.MergedAt) {
			return sorted[i].PR.MergedAt.Before(sorted[j].PR.MergedAt)
		}
		return sorted[i].PR.ID() < sorted[j].PR.ID()
	})
	return Result{
		Assignments: sorted,
		Pool:        len(candidates),
		Selected:    len(selected),
		Targets:     targets,
		Counts:      counts,
	}, nil
}

// stratifiedSelect picks total candidates with round-robin coverage over a
// grid of time bins and lines-changed bins. Within a cell, candidates are
// shuffled with the seed so selection is reproducible.
func stratifiedSelect(candidates []Candidate, total int, seed int64) []Candidate {
	n := len(candidates)

	byTime := append([]Candidate(nil), candidates...)
	sort.Slice(byTime, func(i, j int) bool { return lessByMergedAt(byTime[i].PR, byTime[j].PR) })
	timeBin := make(map[string]int, n)
	for i, c := range byTime {
		timeBin[c.PR.ID()] = i * timeBinCount / n
	}

	bySize := append([]Candidate(nil), candidates...)
	sort.Slice(bySize, func(i, j int) bool {
		li, lj := bySize[i].PR.LinesChanged(), bySize[j].PR.LinesChanged()
		if li != lj {
			return li < lj
		}
		return lessByMergedAt(bySize[i].PR, bySize[j].PR)
	})
	sizeBin := make(map[string]int, n)
	for i, c := range bySize {
		sizeBin[c.PR.ID()] = i * sizeBinCount / n
	}

	type cellKey struct{ time, size int }
	cells := map[cellKey][]Candidate{}
	var keys []cellKey
	for _, c := range candidates {
		k := cellKey{timeBin[c.PR.ID()], sizeBin[c.PR.ID()]}
		if _, ok := cells[k]; !ok {
			keys = append(keys, k)
		}
		cells[k] = append(cells[k], c)
	}

	rng := rand.New(rand.NewSource(seed))
	rng.Shuffle(len(keys), func(i, j int) { keys[i], keys[j] = keys[j], keys[i] })
	for _, k := range keys {
		cell := cells[k]
		rng.Shuffle(len(cell), func(i, j int) { cell[i], cell[j] = cell[j], cell[i] })
	}

	var selected []Candidate
	for len(selected) < total {
		progressed := false
		for _, k := range keys {
			if len(selected) >= total {
				break
			}
			cell := cells[k]
			if len(cell) == 0 {
				continue
			}
			selected = append(selected, cell[0])
			cells[k] = cell[1:]
			progressed = true
		}
		if !progressed {
			break
		}
	}
	return selected
}

// assign places groups into splits with the smallest fill ratio, then moves
// singleton groups to close any remaining gap with the requested sizes.
func assign(selected []Candidate, cfg Config) ([]Assignment, map[Split]int) {
	targets := map[Split]int{Train: cfg.Train, Val: cfg.Val, Test: cfg.Test}
	groups := map[string][]Candidate{}
	var groupKeys []string
	for _, c := range selected {
		g := c.Group
		if !cfg.KeepGroups || g == "" {
			g = c.PR.ID()
		}
		if _, ok := groups[g]; !ok {
			groupKeys = append(groupKeys, g)
		}
		groups[g] = append(groups[g], c)
	}

	rng := rand.New(rand.NewSource(cfg.Seed + 1))
	rng.Shuffle(len(groupKeys), func(i, j int) { groupKeys[i], groupKeys[j] = groupKeys[j], groupKeys[i] })
	sort.SliceStable(groupKeys, func(i, j int) bool {
		return len(groups[groupKeys[i]]) > len(groups[groupKeys[j]])
	})

	counts := map[Split]int{}
	assignments := map[Split][]Candidate{}
	groupSplit := map[string]Split{}
	place := func(g string, split Split) {
		groupSplit[g] = split
		assignments[split] = append(assignments[split], groups[g]...)
		counts[split] += len(groups[g])
	}
	for _, g := range groupKeys {
		place(g, leastFilled(counts, targets))
	}

	// Repair exact counts when singleton groups allow it.
	for moved := 0; moved < len(groupKeys); moved++ {
		over, under := Split(""), Split("")
		for _, s := range splitOrder {
			if over == "" && counts[s] > targets[s] {
				over = s
			}
			if under == "" && counts[s] < targets[s] {
				under = s
			}
		}
		if over == "" || under == "" {
			break
		}
		pick := ""
		for _, g := range groupKeys {
			if groupSplit[g] == over && len(groups[g]) == 1 {
				pick = g
				break
			}
		}
		if pick == "" {
			break
		}
		members := assignments[over]
		for i, c := range members {
			if c.PR.ID() == groups[pick][0].PR.ID() {
				assignments[over] = append(members[:i:i], members[i+1:]...)
				break
			}
		}
		counts[over]--
		place(pick, under)
	}

	var out []Assignment
	for _, split := range splitOrder {
		for _, c := range assignments[split] {
			out = append(out, Assignment{PR: c.PR, SessionCount: c.SessionCount, Group: c.Group, Split: split})
		}
	}
	return out, counts
}

func leastFilled(counts, targets map[Split]int) Split {
	best := splitOrder[0]
	bestRatio := math.Inf(1)
	for _, split := range splitOrder {
		ratio := float64(counts[split]) / float64(targets[split])
		if ratio < bestRatio-1e-9 {
			best = split
			bestRatio = ratio
		}
	}
	return best
}

func splitRank(s Split) int {
	for i, candidate := range splitOrder {
		if candidate == s {
			return i
		}
	}
	return len(splitOrder)
}

func lessByMergedAt(a, b corpus.PullRequest) bool {
	if !a.MergedAt.Equal(b.MergedAt) {
		return a.MergedAt.Before(b.MergedAt)
	}
	return a.ID() < b.ID()
}

// String implements fmt.Stringer for logging.
func (s Split) String() string { return string(s) }
