package prindex

import (
	"sort"

	"github.com/dayvidpham/TraceBench/internal/corpus"
)

// SessionRelation records how a session relates to directly attributed
// sessions through parent, child, or fork lineage. Related lists name the
// linked sessions a context session supports, so a bundle can include the
// full session forest around its attributed work.
type SessionRelation struct {
	SessionID    string   `json:"session_id"`
	Linked       bool     `json:"linked,omitempty"`
	AncestorOf   []string `json:"ancestor_of,omitempty"`
	DescendantOf []string `json:"descendant_of,omitempty"`
	ForkOf       []string `json:"fork_of,omitempty"`
}

// Related returns every linked session this relation points at.
func (r SessionRelation) Related() []string {
	seen := map[string]bool{}
	var out []string
	for _, group := range [][]string{r.AncestorOf, r.DescendantOf, r.ForkOf} {
		for _, id := range group {
			if !seen[id] {
				seen[id] = true
				out = append(out, id)
			}
		}
	}
	sort.Strings(out)
	return out
}

type relationEdge struct {
	target string
	fork   bool
}

// BuildSessionRelations computes the parent, child, and fork closure around
// the linked sessions. Parent edges come from Session.ParentID; fork edges
// come from resolved relationship evidence. It returns one entry per linked
// or related session, sorted by session id.
func BuildSessionRelations(sessions []corpus.Session, evidence []corpus.SessionEdge, linked map[string]bool) []SessionRelation {
	byID := make(map[string]corpus.Session, len(sessions))
	for _, s := range sessions {
		byID[s.ID] = s
	}

	up := map[string][]relationEdge{}
	addUp := func(from, to string, fork bool) {
		if from == "" || to == "" || from == to {
			return
		}
		if _, ok := byID[to]; !ok {
			return
		}
		up[from] = append(up[from], relationEdge{target: to, fork: fork})
	}
	for _, s := range sessions {
		addUp(s.ID, s.ParentID, false)
	}
	for _, e := range evidence {
		addUp(e.From, e.To, true)
	}
	down := map[string][]relationEdge{}
	for from, edges := range up {
		for _, e := range edges {
			down[e.target] = append(down[e.target], relationEdge{target: from, fork: e.fork})
		}
	}

	type state struct {
		id   string
		fork bool
	}
	relations := map[string]*SessionRelation{}
	ensure := func(id string) *SessionRelation {
		if rel, ok := relations[id]; ok {
			return rel
		}
		rel := &SessionRelation{SessionID: id}
		relations[id] = rel
		return rel
	}
	for id := range linked {
		ensure(id).Linked = true
	}

	linkedIDs := make([]string, 0, len(linked))
	for id := range linked {
		linkedIDs = append(linkedIDs, id)
	}
	sort.Strings(linkedIDs)

	for _, root := range linkedIDs {
		walk := func(edges map[string][]relationEdge, ancestors bool) {
			seen := map[state]bool{}
			queue := []state{{id: root}}
			for len(queue) > 0 {
				cur := queue[0]
				queue = queue[1:]
				if seen[cur] {
					continue
				}
				seen[cur] = true
				if cur.id != root {
					rel := ensure(cur.id)
					switch {
					case ancestors && cur.fork:
						rel.ForkOf = appendUnique(rel.ForkOf, root)
					case ancestors:
						rel.AncestorOf = appendUnique(rel.AncestorOf, root)
					case cur.fork:
						rel.ForkOf = appendUnique(rel.ForkOf, root)
					default:
						rel.DescendantOf = appendUnique(rel.DescendantOf, root)
					}
				}
				for _, next := range edges[cur.id] {
					queue = append(queue, state{id: next.target, fork: cur.fork || next.fork})
				}
			}
		}
		walk(up, true)
		walk(down, false)
	}

	out := make([]SessionRelation, 0, len(relations))
	for _, rel := range relations {
		sort.Strings(rel.AncestorOf)
		sort.Strings(rel.DescendantOf)
		sort.Strings(rel.ForkOf)
		out = append(out, *rel)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].SessionID < out[j].SessionID })
	return out
}
