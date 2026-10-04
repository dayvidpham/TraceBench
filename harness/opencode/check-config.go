// Command check-config validates the single parent config, the per-harness
// mapping definition, and the rendered opencode.json. No container.
//
// Default: check-config <config.json> <tool-map.json> <opencode.json>
// Verifies the off/off baseline rendered from the source config.
//
// Perm-only: check-config --perm-only <opencode.json> <want-perm-json>
// Verifies a rendered permission object (used for the search-on case).
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
)

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

func loadJSON(path string, v any) {
	data, err := os.ReadFile(path)
	if err != nil {
		fail("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, v); err != nil {
		fail("parse %s: %v", path, err)
	}
}

func main() {
	if len(os.Args) == 4 && os.Args[1] == "--perm-only" {
		var doc struct {
			Permission map[string]string `json:"permission"`
		}
		loadJSON(os.Args[2], &doc)
		var want map[string]string
		if err := json.Unmarshal([]byte(os.Args[3]), &want); err != nil {
			fail("parse want perm: %v", err)
		}
		if !reflect.DeepEqual(doc.Permission, want) {
			got, _ := json.Marshal(doc.Permission)
			fail("permission is %s, want %s", string(got), os.Args[3])
		}
		return
	}
	if len(os.Args) != 4 {
		fail("usage: check-config <config.json> <tool-map.json> <opencode.json>")
	}
	var config struct {
		Version string          `json:"version"`
		Tools   map[string]bool `json:"tools"`
	}
	loadJSON(os.Args[1], &config)
	if config.Version == "" {
		fail("config version is empty")
	}
	tools := config.Tools
	if !reflect.DeepEqual(tools, map[string]bool{"search": false, "fetch": false}) {
		b, _ := json.Marshal(tools)
		fail("config tools is %s, want search/fetch both false", string(b))
	}
	var mapping map[string]string
	loadJSON(os.Args[2], &mapping)
	if !reflect.DeepEqual(mapping, map[string]string{"search": "websearch", "fetch": "webfetch"}) {
		b, _ := json.Marshal(mapping)
		fail("tool-map.json is %s", string(b))
	}
	var doc map[string]any
	loadJSON(os.Args[3], &doc)
	if len(doc) != 2 {
		fail("opencode.json must have exactly $schema and permission, got %d keys", len(doc))
	}
	schema, _ := doc["$schema"].(string)
	if schema != "https://opencode.ai/config.json" {
		fail("bad $schema: %v", doc["$schema"])
	}
	permRaw, _ := json.Marshal(doc["permission"])
	var perm map[string]string
	if err := json.Unmarshal(permRaw, &perm); err != nil {
		fail("bad permission object: %v", doc["permission"])
	}
	want := map[string]string{"websearch": "deny", "webfetch": "deny"}
	if !reflect.DeepEqual(perm, want) {
		fail("permission is %s, want %s", string(permRaw), `{"webfetch":"deny","websearch":"deny"}`)
	}
	if _, ok := perm["bash"]; ok {
		fail("bash must not be in permission")
	}
}
