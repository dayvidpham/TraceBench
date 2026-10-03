// Command tracebench-sample indexes merged Peasant pull requests, links them
// to agent sessions recorded in a Peasant database, and samples a time- and
// size-stratified train/val/test corpus together with the raw transcripts.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/dayvidpham/TraceBench/internal/collector"
	"github.com/dayvidpham/TraceBench/internal/corpus"
	"github.com/dayvidpham/TraceBench/internal/peasantstore"
	"github.com/dayvidpham/TraceBench/internal/prindex"
	"github.com/dayvidpham/TraceBench/internal/sampler"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return
		}
		fmt.Fprintln(os.Stderr, "tracebench-sample:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 {
		usage()
		return flag.ErrHelp
	}
	switch args[0] {
	case "index":
		return runIndex(args[1:])
	case "sample":
		return runSample(args[1:])
	case "run":
		return runAll(args[1:])
	case "help", "-h", "--help":
		usage()
		return nil
	default:
		usage()
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `tracebench-sample samples a Peasant PR corpus with raw transcripts.

Usage:
  tracebench-sample index  [flags]   index merged PRs and trace links
  tracebench-sample sample [flags]   select splits and collect transcripts
  tracebench-sample run    [flags]   index then sample in one pass

Common flags:
  --db PATH             Peasant SQLite database
  --out DIR             output directory (default "corpus")
  --repo-host PATH      repository checkout recorded in session paths
  --live-repo OWNER/NAME       canonical GitHub repository
  --archive-repo OWNER/NAME    pre-launch archive repository (empty to skip)
  --gh PATH             gh CLI binary
  --ignored-branches LIST      comma-separated non-PR branch names

Index flags:
  --refresh-commits            ignore cached commit-to-PR mappings

Sample flags:
  --train N --val N --test N   split sizes (default 30/10/9)
  --seed N                     sampling seed (default 20261003)
  --keep-groups BOOL           keep same-issue PRs in one split (default true)
  --max-transcript-bytes N     raw file size cap before a database export
  --allow-missing              do not fail on missing or incomplete transcripts
  --dry-run                    print the split without collecting transcripts

Examples:
  tracebench-sample index
  tracebench-sample sample --train 30 --val 10 --test 9
  tracebench-sample run --out corpus
`)
}

type config struct {
	db                 string
	out                string
	repoHost           string
	liveRepo           string
	archiveRepo        string
	gh                 string
	ignored            string
	refreshCommits     bool
	maxTranscriptBytes int64
}

func (c *config) register(fs *flag.FlagSet) {
	home, _ := os.UserHomeDir()
	fs.StringVar(&c.db, "db", filepath.Join(home, ".local/share/peasant/peasant.db"),
		"Peasant SQLite database")
	fs.StringVar(&c.out, "out", "corpus", "output directory")
	fs.StringVar(&c.repoHost, "repo-host", "/home/minttea/dev/peasant-labs/peasant",
		"repository checkout path recorded in session worktree paths")
	fs.StringVar(&c.liveRepo, "live-repo", "peasant-labs/peasant",
		"canonical GitHub repository")
	fs.StringVar(&c.archiveRepo, "archive-repo", "peasant-labs/peasant-prerelease-archive",
		"pre-launch archive GitHub repository (empty to skip)")
	fs.StringVar(&c.gh, "gh", "gh", "gh CLI binary")
	fs.StringVar(&c.ignored, "ignored-branches", strings.Join(prindex.DefaultIgnoredBranches, ","),
		"comma-separated branch names never treated as pull request work")
	fs.BoolVar(&c.refreshCommits, "refresh-commits", false,
		"ignore cached commit-to-pull-request mappings")
	fs.Int64Var(&c.maxTranscriptBytes, "max-transcript-bytes", 512<<20,
		"maximum raw transcript file size before falling back to a database export")
}

type sampleFlags struct {
	train, val, test int
	seed             int64
	keepGroups       bool
	allowMissing     bool
	dryRun           bool
}

func (s *sampleFlags) register(fs *flag.FlagSet) {
	fs.IntVar(&s.train, "train", 30, "train split size")
	fs.IntVar(&s.val, "val", 10, "validation split size")
	fs.IntVar(&s.test, "test", 9, "test split size")
	fs.Int64Var(&s.seed, "seed", 20261003, "sampling seed")
	fs.BoolVar(&s.keepGroups, "keep-groups", true, "keep same-issue pull requests in one split")
	fs.BoolVar(&s.allowMissing, "allow-missing", false,
		"do not fail on missing or incomplete transcript files")
	fs.BoolVar(&s.dryRun, "dry-run", false, "print the split without collecting transcripts")
}

func runIndex(args []string) error {
	cfg := config{}
	fs := flag.NewFlagSet("index", flag.ContinueOnError)
	cfg.register(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	indexDir := filepath.Join(cfg.out, "index")
	idx, err := buildIndex(ctx, cfg, cachedCommitPRs(indexDir))
	if err != nil {
		return err
	}
	if err := prindex.SaveIndex(indexDir, idx); err != nil {
		return err
	}
	printSummary(idx)
	fmt.Printf("wrote %s\n", indexDir)
	return nil
}

func runSample(args []string) error {
	cfg := config{}
	flags := sampleFlags{}
	fs := flag.NewFlagSet("sample", flag.ContinueOnError)
	cfg.register(fs)
	flags.register(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	idx, err := prindex.LoadIndex(filepath.Join(cfg.out, "index"))
	if err != nil {
		return err
	}
	return sampleAndCollect(ctx, cfg, flags, idx)
}

func runAll(args []string) error {
	cfg := config{}
	flags := sampleFlags{}
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	cfg.register(fs)
	flags.register(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	indexDir := filepath.Join(cfg.out, "index")
	idx, err := buildIndex(ctx, cfg, cachedCommitPRs(indexDir))
	if err != nil {
		return err
	}
	if err := prindex.SaveIndex(indexDir, idx); err != nil {
		return err
	}
	printSummary(idx)
	return sampleAndCollect(ctx, cfg, flags, idx)
}

func cachedCommitPRs(indexDir string) map[string][]string {
	previous, err := prindex.LoadIndex(indexDir)
	if err != nil {
		return map[string][]string{}
	}
	return prindex.CachedCommitPRs(previous.Commits)
}

func buildIndex(ctx context.Context, cfg config, cachedCommits map[string][]string) (prindex.Index, error) {
	if cfg.db == "" {
		return prindex.Index{}, errors.New("index: --db must not be empty")
	}
	if cfg.repoHost == "" {
		return prindex.Index{}, errors.New("index: --repo-host must not be empty")
	}
	repos := []string{cfg.liveRepo}
	if cfg.archiveRepo != "" {
		repos = append(repos, cfg.archiveRepo)
	}

	var prs []corpus.PullRequest
	for _, repo := range repos {
		got, err := prindex.FetchMergedPRs(ctx, cfg.gh, repo)
		if err != nil {
			return prindex.Index{}, err
		}
		fmt.Printf("indexed %d merged pull requests for %s\n", len(got), repo)
		prs = append(prs, got...)
	}

	sessions, err := peasantstore.LoadSessions(ctx, cfg.db)
	if err != nil {
		return prindex.Index{}, err
	}
	extractor := prindex.NewKeyExtractor(cfg.repoHost, splitList(cfg.ignored))

	sessionCommits, err := loadSessionCommits(ctx, cfg.db)
	if err != nil {
		return prindex.Index{}, err
	}
	commitResolution, err := prindex.ResolveCommitPRs(
		ctx, cfg.gh, prs, sessionCommits, cachedCommits, cfg.refreshCommits)
	if err != nil {
		return prindex.Index{}, err
	}
	fmt.Printf("session commits: %d hashes, %d mapped to pull requests, %d unresolved\n",
		commitResolution.TotalHashes, commitResolution.Resolved, len(commitResolution.Unresolved))

	traces := prindex.Attribute(sessions, prs, extractor, prindex.LinkInput{
		SessionCommits: sessionCommits,
		CommitPRs:      commitResolution.ByHash,
	})
	if len(traces) == 0 {
		return prindex.Index{}, fmt.Errorf(
			"index: no traces matched; check --repo-host %q against session worktree paths", cfg.repoHost)
	}

	summary := prindex.BuildSummary(sessions, prs, traces, extractor)
	summary.CommitHashes = commitResolution.TotalHashes
	summary.CommitHashesResolved = commitResolution.Resolved
	summary.CommitHashesUnresolved = len(commitResolution.Unresolved)
	return prindex.Index{
		GeneratedAt: time.Now().UTC(),
		RepoHost:    cfg.repoHost,
		Repos:       repos,
		MergedPRs:   prs,
		Sessions:    referencedSessions(sessions, traces),
		Traces:      traces,
		Commits:     commitResolution.Mappings,
		Summary:     summary,
	}, nil
}

func loadSessionCommits(ctx context.Context, dbPath string) (map[string][]string, error) {
	refs, err := peasantstore.LoadSessionCommits(ctx, dbPath)
	if err != nil {
		return nil, err
	}
	sessionCommits := map[string][]string{}
	for _, ref := range refs {
		hashes := sessionCommits[ref.SessionID]
		duplicate := false
		for _, hash := range hashes {
			if hash == ref.Hash {
				duplicate = true
				break
			}
		}
		if !duplicate {
			sessionCommits[ref.SessionID] = append(hashes, ref.Hash)
		}
	}
	return sessionCommits, nil
}

func sampleAndCollect(ctx context.Context, cfg config, flags sampleFlags, idx prindex.Index) error {
	candidates := candidatesFromIndex(idx)
	if len(candidates) == 0 {
		return errors.New("sample: index contains no traced pull requests")
	}
	result, err := sampler.Select(candidates, sampler.Config{
		Train: flags.train, Val: flags.val, Test: flags.test,
		Seed: flags.seed, KeepGroups: flags.keepGroups,
	})
	if err != nil {
		return err
	}
	printSelection(result)
	if flags.dryRun {
		for _, assignment := range result.Assignments {
			fmt.Printf("%-5s %-45s %7d lines %4d sessions\n",
				assignment.Split, assignment.PR.ID(), assignment.PR.LinesChanged(), assignment.SessionCount)
		}
		return nil
	}

	transcripts, err := peasantstore.OpenTranscriptReader(cfg.db, cfg.maxTranscriptBytes)
	if err != nil {
		return err
	}
	defer transcripts.Close()

	manifest, err := collector.Collect(ctx, filepath.Join(cfg.out, "dataset"),
		bundlesFromIndex(idx, result), collector.Options{
			AllowMissing: flags.allowMissing,
			Transcripts:  transcripts,
			Targets:      targetsByName(result.Targets),
			Database:     cfg.db,
			RepoHost:     cfg.repoHost,
			Seed:         flags.seed,
		})
	if err != nil {
		return err
	}
	fmt.Printf("wrote %s: %d pull requests, %d transcripts, %d missing, %d partial\n",
		filepath.Join(cfg.out, "dataset"), len(manifest.PRs), transcriptCount(manifest),
		len(manifest.Missing), len(manifest.Partial))
	return nil
}

func candidatesFromIndex(idx prindex.Index) []sampler.Candidate {
	links := map[string][]corpus.TraceLink{}
	for _, link := range idx.Traces {
		links[link.PRID] = append(links[link.PRID], link)
	}
	sessionsPerPR := map[string]map[string]bool{}
	for prID, prLinks := range links {
		sessionsPerPR[prID] = map[string]bool{}
		for _, link := range prLinks {
			sessionsPerPR[prID][link.SessionID] = true
		}
	}
	var candidates []sampler.Candidate
	for _, pr := range idx.MergedPRs {
		count := len(sessionsPerPR[pr.ID()])
		if count == 0 {
			continue
		}
		candidates = append(candidates, sampler.Candidate{
			PR:           pr,
			SessionCount: count,
			Group:        groupKey(pr),
		})
	}
	sort.Slice(candidates, func(i, j int) bool {
		if !candidates[i].PR.MergedAt.Equal(candidates[j].PR.MergedAt) {
			return candidates[i].PR.MergedAt.Before(candidates[j].PR.MergedAt)
		}
		return candidates[i].PR.ID() < candidates[j].PR.ID()
	})
	return candidates
}

func bundlesFromIndex(idx prindex.Index, result sampler.Result) []collector.Bundle {
	sessionsByID := make(map[string]corpus.Session, len(idx.Sessions))
	for _, s := range idx.Sessions {
		sessionsByID[s.ID] = s
	}
	links := map[string]map[string]corpus.Attribution{}
	for _, link := range idx.Traces {
		if links[link.PRID] == nil {
			links[link.PRID] = map[string]corpus.Attribution{}
		}
		existing, ok := links[link.PRID][link.SessionID]
		if !ok || methodPriority(link.Method) > methodPriority(existing) {
			links[link.PRID][link.SessionID] = link.Method
		}
	}
	bundles := make([]collector.Bundle, 0, len(result.Assignments))
	for _, assignment := range result.Assignments {
		var traces []collector.SessionTrace
		for sessionID, method := range links[assignment.PR.ID()] {
			session, ok := sessionsByID[sessionID]
			if !ok {
				continue
			}
			traces = append(traces, collector.SessionTrace{Session: session, Method: method})
		}
		sort.Slice(traces, func(i, j int) bool {
			if traces[i].Session.StartMS != traces[j].Session.StartMS {
				return traces[i].Session.StartMS < traces[j].Session.StartMS
			}
			return traces[i].Session.ID < traces[j].Session.ID
		})
		bundles = append(bundles, collector.Bundle{Assignment: assignment, Sessions: traces})
	}
	return bundles
}

func methodPriority(m corpus.Attribution) int {
	switch m {
	case corpus.AttributionExact:
		return 4
	case corpus.AttributionCommit:
		return 3
	case corpus.AttributionIssueWindow:
		return 2
	case corpus.AttributionNextMerge:
		return 1
	default:
		return 0
	}
}

func groupKey(pr corpus.PullRequest) string {
	if issue, ok := corpus.IssueFromHeadRef(pr.HeadRef); ok {
		return fmt.Sprintf("%s#issue-%d", pr.Repo, issue)
	}
	return pr.ID()
}

func referencedSessions(sessions []corpus.Session, traces []corpus.TraceLink) []corpus.Session {
	referenced := make(map[string]bool, len(traces))
	for _, link := range traces {
		referenced[link.SessionID] = true
	}
	var out []corpus.Session
	for _, s := range sessions {
		if referenced[s.ID] {
			out = append(out, s)
		}
	}
	return out
}

func targetsByName(targets map[sampler.Split]int) map[string]int {
	out := make(map[string]int, len(targets))
	for split, size := range targets {
		out[string(split)] = size
	}
	return out
}

func transcriptCount(manifest *collector.Manifest) int {
	total := 0
	for _, record := range manifest.PRs {
		total += len(record.Transcripts)
	}
	return total
}

func printSummary(idx prindex.Index) {
	for _, repo := range idx.Summary.Repos {
		fmt.Printf("%s: %d merged PRs, %d traced PRs, %d sessions, %d links\n",
			repo.Repo, repo.MergedPRs, repo.TracedPRs, repo.TracedSessions, repo.Traces)
	}
	fmt.Printf("total: %d merged PRs, %d traced sessions, %d links\n",
		len(idx.MergedPRs), idx.Summary.LinkedSessions, idx.Summary.Traces)
}

func printSelection(result sampler.Result) {
	fmt.Printf("pool: %d traced PRs, selected %d\n", result.Pool, result.Selected)
	for _, split := range []sampler.Split{sampler.Train, sampler.Val, sampler.Test} {
		fmt.Printf("  %-5s %d/%d\n", split, result.Counts[split], result.Targets[split])
		if result.Counts[split] != result.Targets[split] {
			fmt.Fprintf(os.Stderr,
				"warning: %s has %d pull requests but the target is %d; same-issue groups constrain the split\n",
				split, result.Counts[split], result.Targets[split])
		}
	}
}

func splitList(raw string) []string {
	var out []string
	for _, part := range strings.Split(raw, ",") {
		if part = strings.TrimSpace(part); part != "" {
			out = append(out, part)
		}
	}
	return out
}
