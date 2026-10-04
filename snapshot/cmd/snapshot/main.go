// Command snapshot builds a TraceBench repo snapshot (issue #2).
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"

	snap "github.com/dayvidpham/TraceBench/snapshot"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "snapshot:", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	fs := flag.NewFlagSet("snapshot", flag.ContinueOnError)
	repo := fs.String("repo", "", "path to any git repo")
	cutoffType := fs.String("cutoff-type", "", "{date,pr,commit}")
	cutoffDate := fs.String("cutoff-date", "", "ISO date (date cutoff)")
	commit := fs.String("commit", "", "commit SHA (commit cutoff: exact tree)")
	pr := fs.Int("pr", 0, "PR number (pr cutoff)")
	traceDir := fs.String("trace-dir", "", "trace files directory")
	peasantBin := fs.String("peasant-bin", "", "path to peasant binary")
	peasantArgv := fs.String("peasant-argv", "", "override template, '{pr}' = number, '|' separated")
	expectedVersion := fs.String("expected-peasant-version", "", "fail unless peasant --version contains this")
	prStartOverride := fs.String("pr-start-override", "", "ISO PR start (bypass binary)")
	out := fs.String("out", "", "write history.json (+ repo/ with --materialize)")
	materialize := fs.Bool("materialize", false, "also write repo/ tree and traces/")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *repo == "" {
		return fmt.Errorf("--repo is required")
	}
	var cutoff snap.Cutoff
	var err error
	switch *cutoffType {
	case "date":
		if *cutoffDate == "" {
			return fmt.Errorf("--cutoff-date is required for date cutoffs")
		}
		cutoff, err = snap.ByDate(*cutoffDate)
	case "pr":
		if *pr == 0 {
			return fmt.Errorf("--pr is required for pr cutoffs")
		}
		cutoff, err = snap.ByPR(*pr)
	case "commit":
		if *commit == "" {
			return fmt.Errorf("--commit is required for commit cutoffs")
		}
		cutoff, err = snap.ByCommit(*commit)
	default:
		return fmt.Errorf("--cutoff-type must be date, pr, or commit (got %q)", *cutoffType)
	}
	if err != nil {
		return err
	}

	var peasant snap.PeasantClient
	if cutoff.Kind == snap.CutoffPR && *prStartOverride == "" {
		var template []string
		if *peasantArgv != "" {
			template = strings.Split(*peasantArgv, "|")
		}
		bin := *peasantBin
		if bin == "" {
			bin = "peasant"
		}
		client := snap.BinaryPeasantClient{
			Binary: bin, PRShowArgv: template, ExpectedVersion: *expectedVersion,
		}
		if *expectedVersion != "" {
			if _, err := client.Version(); err != nil {
				return err
			}
		}
		peasant = client
	}

	s, err := snap.SnapshotRepo(*repo, cutoff, snap.Options{
		TraceDir: *traceDir, Peasant: peasant, PRStartOverride: *prStartOverride,
	})
	if err != nil {
		return err
	}
	if *out != "" {
		if *materialize {
			path, err := snap.Materialize(*repo, s, *out, *traceDir)
			if err != nil {
				return err
			}
			fmt.Println(path)
			return nil
		}
		path, err := snap.WriteSnapshot(s, *out)
		if err != nil {
			return err
		}
		fmt.Println(path)
		return nil
	}
	payload, err := snap.CLIPayload(s)
	if err != nil {
		return err
	}
	pretty, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	fmt.Println(string(pretty))
	return nil
}
