// Package fetch downloads a TraceBench dump from a HuggingFace dataset
// repository and verifies its content hashes.
package fetch

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	"github.com/peasant-labs/schema"

	"github.com/dayvidpham/TraceBench/internal/dump"
)

// Options controls a fetch.
type Options struct {
	Repo        string
	Revision    string
	Endpoint    string
	Token       string
	Dir         string
	Concurrency int
	Verify      bool
	Client      *http.Client
}

// Result reports what was downloaded.
type Result struct {
	Sessions int
	Traces   int
	Verified int
	Bytes    int64
}

// Fetch downloads the dump files into Options.Dir. When Verify is set, each
// transcript's SHA3-256 hash must match its metadata record, and any mismatch
// or download failure fails the fetch.
func Fetch(ctx context.Context, opts Options) (*Result, error) {
	if opts.Repo == "" {
		return nil, errors.New("fetch: repository is required")
	}
	if opts.Dir == "" {
		return nil, errors.New("fetch: destination directory is required")
	}
	if opts.Revision == "" {
		opts.Revision = "main"
	}
	if opts.Endpoint == "" {
		opts.Endpoint = "https://huggingface.co"
	}
	if opts.Concurrency <= 0 {
		opts.Concurrency = 8
	}
	if opts.Client == nil {
		opts.Client = &http.Client{}
	}
	if err := os.MkdirAll(filepath.Join(opts.Dir, "transcripts"), 0o755); err != nil {
		return nil, fmt.Errorf("create destination %s: %w", opts.Dir, err)
	}

	base := strings.TrimSuffix(opts.Endpoint, "/") + "/datasets/" + opts.Repo +
		"/resolve/" + url.PathEscape(opts.Revision)
	f := &fetcher{client: opts.Client, token: opts.Token, base: base}

	manifestBytes, err := f.get(ctx, "manifest.json")
	if err != nil {
		return nil, err
	}
	var manifest dump.Manifest
	if err := json.Unmarshal(manifestBytes, &manifest); err != nil {
		return nil, fmt.Errorf("decode manifest.json: %w", err)
	}

	metadataBytes, err := f.get(ctx, "metadata.jsonl")
	if err != nil {
		return nil, err
	}
	metadata, err := parseMetadata(metadataBytes)
	if err != nil {
		return nil, err
	}
	if manifest.Sessions > 0 && len(metadata) != manifest.Sessions {
		return nil, fmt.Errorf("fetch: manifest lists %d sessions but metadata.jsonl carries %d",
			manifest.Sessions, len(metadata))
	}

	result := &Result{Sessions: len(metadata)}
	if err := f.downloadTo(ctx, "metadata.jsonl", filepath.Join(opts.Dir, "metadata.jsonl"), &result.Bytes); err != nil {
		return nil, err
	}
	if err := f.downloadTo(ctx, "manifest.json", filepath.Join(opts.Dir, "manifest.json"), nil); err != nil {
		return nil, err
	}
	for _, name := range []string{"pull_requests.jsonl", "traces.jsonl"} {
		dest := filepath.Join(opts.Dir, name)
		data, err := f.get(ctx, name)
		if err != nil {
			return nil, err
		}
		if err := os.WriteFile(dest, data, 0o644); err != nil {
			return nil, fmt.Errorf("write %s: %w", dest, err)
		}
		result.Bytes += int64(len(data))
		if name == "traces.jsonl" {
			result.Traces = countLines(data)
		}
	}

	ids := make([]string, 0, len(metadata))
	for id := range metadata {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	var (
		wg       sync.WaitGroup
		mu       sync.Mutex
		errs     []error
		sem      = make(chan struct{}, opts.Concurrency)
		verified int
		total    int64
	)
	for _, id := range ids {
		if err := ctx.Err(); err != nil {
			break
		}
		id := id
		wg.Add(1)
		sem <- struct{}{}
		go func() {
			defer wg.Done()
			defer func() { <-sem }()
			data, err := f.get(ctx, "transcripts/"+id+".jsonl")
			if err != nil {
				mu.Lock()
				errs = append(errs, err)
				mu.Unlock()
				return
			}
			if opts.Verify {
				if got := schema.ComputeTranscriptHash(data); got != metadata[id].ContentHash {
					mu.Lock()
					errs = append(errs, fmt.Errorf("transcript %s: content hash mismatch", id))
					mu.Unlock()
					return
				}
			}
			dest := filepath.Join(opts.Dir, "transcripts", id+".jsonl")
			if err := os.WriteFile(dest, data, 0o644); err != nil {
				mu.Lock()
				errs = append(errs, fmt.Errorf("write %s: %w", dest, err))
				mu.Unlock()
				return
			}
			mu.Lock()
			verified++
			total += int64(len(data))
			mu.Unlock()
		}()
	}
	wg.Wait()
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if len(errs) > 0 {
		return nil, fmt.Errorf("fetch: %w", errors.Join(errs...))
	}
	if opts.Verify {
		result.Verified = verified
	}
	result.Bytes += total
	return result, nil
}

type fetcher struct {
	client *http.Client
	token  string
	base   string
}

func (f *fetcher) get(ctx context.Context, path string) ([]byte, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, f.base+"/"+path, nil)
	if err != nil {
		return nil, fmt.Errorf("build request for %s: %w", path, err)
	}
	if f.token != "" {
		request.Header.Set("Authorization", "Bearer "+f.token)
	}
	response, err := f.client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("get %s: %w", path, err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("get %s: %s", path, response.Status)
	}
	data, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	return data, nil
}

func (f *fetcher) downloadTo(ctx context.Context, remote, dest string, size *int64) error {
	data, err := f.get(ctx, remote)
	if err != nil {
		return err
	}
	if err := os.WriteFile(dest, data, 0o644); err != nil {
		return fmt.Errorf("write %s: %w", dest, err)
	}
	if size != nil {
		*size += int64(len(data))
	}
	return nil
}

func parseMetadata(data []byte) (map[string]schema.UnifiedMetadata, error) {
	out := map[string]schema.UnifiedMetadata{}
	scanner := bufio.NewScanner(bytes.NewReader(data))
	scanner.Buffer(make([]byte, 64*1024), 64*1024*1024)
	line := 0
	for scanner.Scan() {
		line++
		if len(bytes.TrimSpace(scanner.Bytes())) == 0 {
			continue
		}
		var meta schema.UnifiedMetadata
		if err := json.Unmarshal(scanner.Bytes(), &meta); err != nil {
			return nil, fmt.Errorf("decode metadata line %d: %w", line, err)
		}
		out[meta.SessionID.String()] = meta
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read metadata.jsonl: %w", err)
	}
	return out, nil
}

func countLines(data []byte) int {
	count := 0
	for _, line := range bytes.Split(data, []byte("\n")) {
		if len(bytes.TrimSpace(line)) > 0 {
			count++
		}
	}
	return count
}
