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

	"github.com/peasant-labs/redact"
	"github.com/peasant-labs/schema"

	"github.com/dayvidpham/TraceBench/internal/collector"
	"github.com/dayvidpham/TraceBench/internal/corpus"
	"github.com/dayvidpham/TraceBench/internal/dump"
	"github.com/dayvidpham/TraceBench/internal/fetch"
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
	case "dump":
		return runDump(args[1:])
	case "fetch":
		return runFetch(args[1:])
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
  tracebench-sample dump   [flags]   write a flat publishable dump
  tracebench-sample fetch  [flags]   download the published corpus from HuggingFace

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
  --pin-prs FILE               pin the sample to a newline-delimited PR id list
  --max-transcript-bytes N     raw file size cap before a database export
  --allow-missing              do not fail on missing or incomplete transcripts
  --dry-run                    print the split without collecting transcripts

Dump flags:
  --source local|village-pull  dump input (default "local")
  --peasant PATH               peasant CLI binary (default "peasant")
  --peasant-data-dir DIR       peasant data directory holding peasant/peasant.db
  --redaction-level LEVEL      redaction level (default "standard")
  --push-contract-version VER  transcript envelope contract (default "0.1.1")
  --allow-missing              keep going when a transcript cannot be produced

Fetch flags:
  --repo OWNER/NAME            HuggingFace dataset repository (default "dayvidpham/TraceBench")
  --revision REV               dataset revision (default "main")
  --dest DIR                   destination directory (default "hf")
  --concurrency N              parallel transcript downloads (default 8)
  --endpoint URL               HuggingFace endpoint (default "https://huggingface.co")
  --token TOKEN                access token (defaults to HF_TOKEN)
  --no-verify                  skip content hash verification

Examples:
  tracebench-sample index
  tracebench-sample sample --train 30 --val 10 --test 9
  tracebench-sample dump
  tracebench-sample dump --source village-pull
  tracebench-sample fetch --dest data/tracebench
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
		"pre-launch archive GitHub repository (indexed for reference; never sampled; empty to skip)")
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
	pinPRs           string
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
	fs.StringVar(&s.pinPRs, "pin-prs", "",
		"newline-delimited pull request ids to pin the sample to (reproducing a previous sample)")
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

type dumpFlags struct {
	source              string
	peasant             string
	peasantDataDir      string
	redactionLevel      string
	pushContractVersion string
	allowMissing        bool
}

func (d *dumpFlags) register(fs *flag.FlagSet) {
	fs.StringVar(&d.source, "source", string(dump.SourceLocal), "dump input: local or village-pull")
	fs.StringVar(&d.peasant, "peasant", "peasant", "peasant CLI binary")
	fs.StringVar(&d.peasantDataDir, "peasant-data-dir", "",
		"peasant data directory holding peasant/peasant.db (derived from --db when omitted)")
	fs.StringVar(&d.redactionLevel, "redaction-level", string(redact.Standard), "redaction level")
	fs.StringVar(&d.pushContractVersion, "push-contract-version", "0.1.1",
		"transcript envelope contract version")
	fs.BoolVar(&d.allowMissing, "allow-missing", false,
		"keep going when a transcript cannot be produced")
}

func runDump(args []string) error {
	cfg := config{}
	flags := dumpFlags{}
	fs := flag.NewFlagSet("dump", flag.ContinueOnError)
	cfg.register(fs)
	flags.register(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	opts := dump.Options{
		Source:              dump.Source(flags.source),
		Database:            cfg.db,
		PeasantBin:          flags.peasant,
		RedactionLevel:      redact.RedactionLevel(flags.redactionLevel),
		PushContractVersion: schema.PushContractVersion(flags.pushContractVersion),
		XDG:                 xdgPaths(),
		AllowMissing:        flags.allowMissing,
	}
	dumpDir := filepath.Join(cfg.out, "dump")
	if opts.Source == dump.SourceVillagePull {
		dumpDir = filepath.Join(cfg.out, "dump-village-pull")
	}
	switch opts.Source {
	case dump.SourceLocal:
		dataDir, err := resolvePeasantDataDir(cfg.db, flags.peasantDataDir)
		if err != nil {
			return err
		}
		opts.PeasantDataDir = dataDir
		return dumpLocal(ctx, cfg, opts, dumpDir)
	case dump.SourceVillagePull:
		return dumpVillagePull(ctx, cfg, opts, dumpDir)
	default:
		return fmt.Errorf("dump: unknown --source %q (want local or village-pull)", flags.source)
	}
}

func dumpLocal(ctx context.Context, cfg config, opts dump.Options, dumpDir string) error {
	manifest, err := collector.LoadManifest(filepath.Join(cfg.out, "dataset", "manifest.json"))
	if err != nil {
		return fmt.Errorf("load sampled dataset (run 'tracebench-sample sample' first): %w", err)
	}
	opts.Seed = manifest.Seed
	opts.Targets = manifest.Targets
	opts.SplitCounts = manifest.Counts

	var prs []dump.PRRecord
	var traces []dump.TraceRecord
	sessionSet := map[string]bool{}
	for _, pr := range manifest.PRs {
		linked := 0
		for _, transcript := range pr.Transcripts {
			if transcript.Method != corpus.AttributionContext {
				linked++
			}
			traces = append(traces, dump.TraceRecord{
				PR:        pr.ID,
				SessionID: transcript.SessionID,
				Method:    string(transcript.Method),
				Relation:  transcript.Relation,
				Split:     string(pr.Split),
			})
			sessionSet[transcript.SessionID] = true
		}
		prs = append(prs, dump.PRRecord{
			ID: pr.ID, Repo: pr.Repo, Number: pr.Number, Title: pr.Title, Body: pr.Body,
			Issue: pr.Issue, URL: pr.URL,
			Author: pr.Author, HeadRef: pr.HeadRef, MergedAt: pr.MergedAt,
			Additions: pr.Additions, Deletions: pr.Deletions, LinesChanged: pr.LinesChanged,
			Split: string(pr.Split), Group: pr.Group,
			LinkedSessions: linked, TotalSessions: len(pr.Transcripts),
		})
	}
	ids := make([]string, 0, len(sessionSet))
	for id := range sessionSet {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	artifacts, err := peasantstore.LoadSessionArtifacts(ctx, cfg.db, ids)
	if err != nil {
		return err
	}
	artifactByID := make(map[string]corpus.SessionArtifact, len(artifacts))
	for _, artifact := range artifacts {
		artifactByID[artifact.ID] = artifact
	}

	w, err := dump.NewWriter(dumpDir, opts)
	if err != nil {
		return err
	}
	payloads, failures, err := (&dump.PeasantExport{
		Bin:     opts.PeasantBin,
		DataDir: opts.PeasantDataDir,
		WorkDir: dumpDir + "-work",
	}).Payloads(ctx, ids)
	if err != nil {
		return err
	}

	var problems []error
	for _, id := range ids {
		if err := ctx.Err(); err != nil {
			return err
		}
		artifact, ok := artifactByID[id]
		if !ok {
			failure := errors.New("session not found in the peasant database")
			w.Fail(id, failure)
			problems = append(problems, fmt.Errorf("session %s: %w", id, failure))
			continue
		}
		payload, ok := payloads[id]
		if !ok {
			message := failures[id]
			if message == "" {
				message = "no payload produced"
			}
			failure := errors.New(message)
			w.Fail(id, failure)
			problems = append(problems, fmt.Errorf("session %s: %w", id, failure))
			continue
		}
		if err := w.Add(artifact, payload); err != nil {
			w.Fail(id, err)
			problems = append(problems, fmt.Errorf("session %s: %w", id, err))
		}
	}
	if err := w.SetIndexes(prs, traces); err != nil {
		return err
	}
	written, err := w.Finish()
	if err != nil {
		return err
	}
	fmt.Printf("wrote %s: %d sessions, %d pull requests, %d traces, %d failed\n",
		dumpDir, written.Sessions, written.PullRequests, written.Traces, len(written.FailedSessions))
	if len(problems) > 0 && !opts.AllowMissing {
		return fmt.Errorf("dump: %w", errors.Join(problems...))
	}
	return nil
}

func dumpVillagePull(ctx context.Context, cfg config, opts dump.Options, dumpDir string) error {
	pulls, err := peasantstore.LoadPulledTranscripts(ctx, cfg.db)
	if err != nil {
		return err
	}
	if len(pulls) == 0 {
		return errors.New("dump: no pulled transcripts found; run 'peasant village pull' first")
	}

	w, err := dump.NewWriter(dumpDir, opts)
	if err != nil {
		return err
	}
	var infos []schema.PullTranscriptInfo
	var problems []error
	for _, pull := range pulls {
		if err := ctx.Err(); err != nil {
			return err
		}
		info, detail, payload, err := dump.ReadPullBundle(pull)
		if err != nil {
			w.Fail(pull.TranscriptID, err)
			problems = append(problems, fmt.Errorf("transcript %s: %w", pull.TranscriptID, err))
			continue
		}
		if err := w.AddVillage(pull, detail, payload); err != nil {
			w.Fail(pull.TranscriptID, err)
			problems = append(problems, fmt.Errorf("transcript %s: %w", pull.TranscriptID, err))
			continue
		}
		infos = append(infos, info)
	}
	if err := w.SetVillagePulls(infos); err != nil {
		return err
	}
	written, err := w.Finish()
	if err != nil {
		return err
	}
	fmt.Printf("wrote %s: %d collective transcripts, %d failed\n",
		dumpDir, written.Sessions, len(written.FailedSessions))
	if len(problems) > 0 && !opts.AllowMissing {
		return fmt.Errorf("dump: %w", errors.Join(problems...))
	}
	return nil
}

func resolvePeasantDataDir(dbPath, override string) (string, error) {
	if override != "" {
		return override, nil
	}
	parent := filepath.Dir(dbPath)
	if filepath.Base(parent) == "peasant" {
		return filepath.Dir(parent), nil
	}
	return "", fmt.Errorf(
		"dump: cannot derive the peasant data directory from --db %s; pass --peasant-data-dir", dbPath)
}

func xdgPaths() redact.XDGPaths {
	home, _ := os.UserHomeDir()
	pick := func(env, fallback string) string {
		if value := os.Getenv(env); value != "" {
			return value
		}
		return filepath.Join(home, fallback)
	}
	return redact.XDGPaths{
		DataHome:   pick("XDG_DATA_HOME", ".local/share"),
		ConfigHome: pick("XDG_CONFIG_HOME", ".config"),
		StateHome:  pick("XDG_STATE_HOME", ".local/state"),
	}
}

type fetchFlags struct {
	repo        string
	revision    string
	endpoint    string
	token       string
	dest        string
	concurrency int
	noVerify    bool
}

func (f *fetchFlags) register(fs *flag.FlagSet) {
	fs.StringVar(&f.repo, "repo", "dayvidpham/TraceBench", "HuggingFace dataset repository")
	fs.StringVar(&f.revision, "revision", "main", "dataset revision (branch, tag, or commit)")
	fs.StringVar(&f.endpoint, "endpoint", "https://huggingface.co", "HuggingFace endpoint")
	fs.StringVar(&f.token, "token", "", "access token (defaults to HF_TOKEN)")
	fs.StringVar(&f.dest, "dest", "hf", "destination directory")
	fs.IntVar(&f.concurrency, "concurrency", 8, "parallel transcript downloads")
	fs.BoolVar(&f.noVerify, "no-verify", false, "skip content hash verification")
}

func runFetch(args []string) error {
	flags := fetchFlags{}
	fs := flag.NewFlagSet("fetch", flag.ContinueOnError)
	flags.register(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	token := flags.token
	if token == "" {
		token = os.Getenv("HF_TOKEN")
	}
	if token == "" {
		token = os.Getenv("HUGGING_FACE_HUB_TOKEN")
	}
	result, err := fetch.Fetch(ctx, fetch.Options{
		Repo:        flags.repo,
		Revision:    flags.revision,
		Endpoint:    flags.endpoint,
		Token:       token,
		Dir:         flags.dest,
		Concurrency: flags.concurrency,
		Verify:      !flags.noVerify,
	})
	if err != nil {
		return err
	}
	fmt.Printf("fetched %s@%s: %d sessions, %d traces, %d verified, %.1f MiB into %s\n",
		flags.repo, flags.revision, result.Sessions, result.Traces, result.Verified,
		float64(result.Bytes)/(1<<20), flags.dest)
	return nil
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

	edges, err := peasantstore.LoadSessionEdges(ctx, cfg.db)
	if err != nil {
		return prindex.Index{}, err
	}
	linked := make(map[string]bool, len(traces))
	for _, link := range traces {
		linked[link.SessionID] = true
	}
	relations := prindex.BuildSessionRelations(sessions, edges, linked)
	relatedCount := 0
	for _, relation := range relations {
		if !relation.Linked {
			relatedCount++
		}
	}

	summary := prindex.BuildSummary(sessions, prs, traces, extractor)
	summary.CommitHashes = commitResolution.TotalHashes
	summary.CommitHashesResolved = commitResolution.Resolved
	summary.CommitHashesUnresolved = len(commitResolution.Unresolved)
	summary.RelatedSessions = relatedCount
	return prindex.Index{
		GeneratedAt: time.Now().UTC(),
		RepoHost:    cfg.repoHost,
		Repos:       repos,
		MergedPRs:   prs,
		Sessions:    closureSessions(sessions, relations),
		Traces:      traces,
		Commits:     commitResolution.Mappings,
		Relations:   relations,
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
	candidates := sampler.ExcludeRepos(candidatesFromIndex(idx), cfg.archiveRepo)
	if len(candidates) == 0 {
		return errors.New("sample: index contains no traced pull requests outside the excluded repositories")
	}
	if flags.pinPRs != "" {
		pinned, err := readPinnedPRs(flags.pinPRs)
		if err != nil {
			return err
		}
		filtered := candidates[:0]
		for _, candidate := range candidates {
			if pinned[candidate.PR.ID()] {
				filtered = append(filtered, candidate)
			}
		}
		missing := 0
		for id := range pinned {
			found := false
			for _, candidate := range candidates {
				if candidate.PR.ID() == id {
					found = true
					break
				}
			}
			if !found {
				missing++
			}
		}
		if missing > 0 {
			return fmt.Errorf("sample: %d pinned pull requests are not traced in the current index", missing)
		}
		candidates = filtered
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
	contextFor := map[string][]string{}
	for _, relation := range idx.Relations {
		for _, linkedID := range relation.Related() {
			contextFor[linkedID] = append(contextFor[linkedID], relation.SessionID)
		}
	}
	relationByID := make(map[string]prindex.SessionRelation, len(idx.Relations))
	for _, relation := range idx.Relations {
		relationByID[relation.SessionID] = relation
	}

	bundles := make([]collector.Bundle, 0, len(result.Assignments))
	for _, assignment := range result.Assignments {
		prLinks := links[assignment.PR.ID()]
		linkedSet := make(map[string]bool, len(prLinks))
		var traces []collector.SessionTrace
		for sessionID, method := range prLinks {
			linkedSet[sessionID] = true
			if session, ok := sessionsByID[sessionID]; ok {
				traces = append(traces, collector.SessionTrace{Session: session, Method: method, Relation: "linked"})
			}
		}
		seen := map[string]bool{}
		for sessionID := range prLinks {
			for _, contextID := range contextFor[sessionID] {
				if seen[contextID] || linkedSet[contextID] {
					continue
				}
				seen[contextID] = true
				relation := contextRelation(relationByID[contextID], linkedSet)
				if relation == "" {
					continue
				}
				if session, ok := sessionsByID[contextID]; ok {
					traces = append(traces, collector.SessionTrace{
						Session:  session,
						Method:   corpus.AttributionContext,
						Relation: relation,
					})
				}
			}
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

func contextRelation(relation prindex.SessionRelation, linkedSet map[string]bool) string {
	if intersects(relation.AncestorOf, linkedSet) {
		return "ancestor"
	}
	if intersects(relation.DescendantOf, linkedSet) {
		return "descendant"
	}
	if intersects(relation.ForkOf, linkedSet) {
		return "fork"
	}
	return ""
}

func intersects(ids []string, set map[string]bool) bool {
	for _, id := range ids {
		if set[id] {
			return true
		}
	}
	return false
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

func closureSessions(sessions []corpus.Session, relations []prindex.SessionRelation) []corpus.Session {
	keep := make(map[string]bool, len(relations))
	for _, relation := range relations {
		keep[relation.SessionID] = true
	}
	var out []corpus.Session
	for _, s := range sessions {
		if keep[s.ID] {
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
	fmt.Printf("total: %d merged PRs, %d linked sessions, %d related sessions, %d links\n",
		len(idx.MergedPRs), idx.Summary.LinkedSessions, idx.Summary.RelatedSessions, idx.Summary.Traces)
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

func readPinnedPRs(path string) (map[string]bool, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read pinned pull requests %s: %w", path, err)
	}
	pinned := map[string]bool{}
	for _, line := range strings.Split(string(data), "\n") {
		if line = strings.TrimSpace(line); line != "" {
			pinned[line] = true
		}
	}
	if len(pinned) == 0 {
		return nil, fmt.Errorf("pinned pull request list %s is empty", path)
	}
	return pinned, nil
}
