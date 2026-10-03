// Command render builds harness/opencode/opencode.json from the single
// parent-level toggle (harness/tools.json) and the per-harness mapping
// definition (harness/opencode/tool-map.json).
//
// Only search/fetch may be toggled. bash is never representable.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
)

var allowed = []string{"fetch", "search"}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

func loadObject(path string) map[string]json.RawMessage {
	data, err := os.ReadFile(path)
	if err != nil {
		fail("read %s: %v", path, err)
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(data, &obj); err != nil {
		fail("parse %s: %v", path, err)
	}
	return obj
}

func main() {
	if len(os.Args) != 4 {
		fail("usage: render <tools.json> <tool-map.json> <out.json>")
	}
	toolsPath, mapPath, outPath := os.Args[1], os.Args[2], os.Args[3]

	toolsRaw := loadObject(toolsPath)
	if len(toolsRaw) != len(allowed) {
		fail("tools must be exactly [fetch search], got %d keys", len(toolsRaw))
	}
	tools := map[string]bool{}
	for _, name := range allowed {
		raw, ok := toolsRaw[name]
		if !ok {
			fail("tools must be exactly [fetch search], missing %q", name)
		}
		var enabled bool
		// Strict bool check: strings like "off" must fail with "must be bool".
		var probe any
		if err := json.Unmarshal(raw, &probe); err != nil {
			fail("tool %q must be bool: %s", name, string(raw))
		}
		if _, ok := probe.(bool); !ok {
			fail("tool %q must be bool, got %s", name, string(raw))
		}
		if err := json.Unmarshal(raw, &enabled); err != nil {
			fail("tool %q must be bool, got %s", name, string(raw))
		}
		tools[name] = enabled
	}
	for name := range toolsRaw {
		if name != "fetch" && name != "search" {
			fail("tools must be exactly [fetch search], got unexpected %q", name)
		}
	}

	mapRaw := loadObject(mapPath)
	if len(mapRaw) != len(allowed) {
		fail("map must be exactly [fetch search], got %d keys", len(mapRaw))
	}
	mapping := map[string]string{}
	for _, name := range allowed {
		raw, ok := mapRaw[name]
		if !ok {
			fail("map must be exactly [fetch search], missing %q", name)
		}
		var tool string
		if err := json.Unmarshal(raw, &tool); err != nil || tool == "" {
			fail("map %q must be a non-empty string", name)
		}
		if tool == "bash" {
			fail("refusing to map bash")
		}
		mapping[name] = tool
	}
	for name := range mapRaw {
		if name != "fetch" && name != "search" {
			fail("map must be exactly [fetch search], got unexpected %q", name)
		}
	}
	seen := map[string]string{}
	for _, name := range allowed {
		tool := mapping[name]
		if prev, dup := seen[tool]; dup {
			fail("duplicate permission key: %q (from %q and %q)", tool, prev, name)
		}
		seen[tool] = name
		if tool == "bash" {
			fail("refusing to deny bash")
		}
	}

	permKeys := []string{}
	for _, name := range allowed {
		if !tools[name] {
			tool := mapping[name]
			if tool == "bash" {
				fail("refusing to deny bash")
			}
			permKeys = append(permKeys, tool)
		}
	}
	sort.Strings(permKeys)

	out := struct {
		Schema     string            `json:"$schema"`
		Permission map[string]string `json:"permission"`
	}{
		Schema:     "https://opencode.ai/config.json",
		Permission: map[string]string{},
	}
	for _, k := range permKeys {
		out.Permission[k] = "deny"
	}

	// Deterministic key order: $schema, then permission with sorted keys.
	buf := "{\n"
	schemaBytes, _ := json.Marshal(out.Schema)
	buf += fmt.Sprintf("  \"$schema\": %s,\n", string(schemaBytes))
	buf += "  \"permission\": {"
	if len(permKeys) == 0 {
		buf += "}"
	} else {
		buf += "\n"
		for i, k := range permKeys {
			kb, _ := json.Marshal(k)
			vb, _ := json.Marshal("deny")
			comma := ","
			if i == len(permKeys)-1 {
				comma = ""
			}
			buf += fmt.Sprintf("    %s: %s%s\n", string(kb), string(vb), comma)
		}
		buf += "  }"
	}
	buf += "\n}\n"

	if err := os.WriteFile(outPath, []byte(buf), 0644); err != nil {
		fail("write %s: %v", outPath, err)
	}
}
