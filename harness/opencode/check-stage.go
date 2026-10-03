// Command check-stage verifies the agent stage log written by agent.py.
// Usage: check-stage <bare-ref>  (e.g. 1.18.34, without the leading v)
// Finds the newest jobs/**/opencode-stage.txt, prints it, and requires the
// expected version/permission/tool lines.
package main

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

func main() {
	if len(os.Args) != 2 {
		fail("usage: check-stage <bare-ref>")
	}
	ref := os.Args[1]
	var newest string
	var newestMtime int64 = -1
	err := filepath.WalkDir("jobs", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() || d.Name() != "opencode-stage.txt" {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return nil
		}
		if t := info.ModTime().UnixNano(); t > newestMtime {
			newestMtime = t
			newest = path
		}
		return nil
	})
	if err != nil {
		fail("walk jobs: %v", err)
	}
	if newest == "" {
		fail("no opencode-stage.txt under jobs/")
	}
	data, err := os.ReadFile(newest)
	if err != nil {
		fail("read %s: %v", newest, err)
	}
	text := string(data)
	fmt.Println(newest)
	fmt.Print(text)
	need := []string{
		"version: " + ref,
		"websearch: deny",
		"webfetch: deny",
		"bash: absent",
		"webfetch-tool: disabled",
		"websearch-tool: disabled",
		"bash-tool: enabled",
		"auto: explicit deny holds",
		"connect: failed",
	}
	var missing []string
	for _, line := range need {
		if !strings.Contains(text, line) {
			missing = append(missing, line)
		}
	}
	if len(missing) > 0 {
		fail("agent stage log missing: %s", strings.Join(missing, ", "))
	}
}
