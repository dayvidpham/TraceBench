package snapshot

import (
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"time"
)

// TraceFile is one trace artifact with its event time.
// The trace store is not available yet; the contract is a bunch of files
// the coding agent can access during execution.
type TraceFile struct {
	Path      string    `json:"path"`
	EventTime time.Time `json:"event_time"`
	Size      int64     `json:"size"`
}

// TraceProvider lists trace files visible at a cutoff.
type TraceProvider interface {
	ListFiles(cutoff time.Time) ([]TraceFile, error)
}

// StubTraceProvider returns no traces (default; keeps #2 unblocked).
type StubTraceProvider struct{}

func (StubTraceProvider) ListFiles(time.Time) ([]TraceFile, error) { return nil, nil }

// DirTraceProvider walks a directory, using file mtime as event time and
// keeping files with mtime <= cutoff. This matches "bunch of files" and
// lets us test the cutoff rule before the real store lands.
type DirTraceProvider struct {
	Root string
}

func (d DirTraceProvider) ListFiles(cutoff time.Time) ([]TraceFile, error) {
	cutoff = cutoff.UTC()
	if _, err := os.Stat(d.Root); os.IsNotExist(err) {
		return nil, nil
	}
	var out []TraceFile
	err := filepath.WalkDir(d.Root, func(path string, de fs.DirEntry, err error) error {
		if err != nil || de.IsDir() {
			return nil
		}
		fi, err := de.Info()
		if err != nil {
			return nil
		}
		event := fi.ModTime().UTC()
		if event.After(cutoff) {
			return nil
		}
		rel, err := filepath.Rel(d.Root, path)
		if err != nil {
			return nil
		}
		out = append(out, TraceFile{Path: rel, EventTime: event, Size: fi.Size()})
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Path < out[j].Path })
	return out, nil
}

// MaterializeTraces copies the snapshot's trace files into dest.
func MaterializeTraces(root string, files []TraceFile, dest string) error {
	for _, f := range files {
		src := filepath.Join(root, f.Path)
		dst := filepath.Join(dest, f.Path)
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return err
		}
		in, err := os.Open(src)
		if err != nil {
			return err
		}
		out, err := os.Create(dst)
		if err != nil {
			in.Close()
			return err
		}
		_, cpErr := io.Copy(out, in)
		cErr := out.Close()
		iErr := in.Close()
		if cpErr != nil {
			return cpErr
		}
		if cErr != nil {
			return cErr
		}
		if iErr != nil {
			return iErr
		}
	}
	return nil
}
