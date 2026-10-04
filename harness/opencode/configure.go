// Command configure validates the parent harness config and translates its
// search/fetch toggles through this harness's local tool map.
//
// Usage:
//
//	configure version <config.json>
//	configure render <config.json> <tool-map.json> <out-config.json>
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

func loadConfig(configPath string) (string, map[string]bool) {
	configRaw := loadObject(configPath)
	if len(configRaw) != 2 {
		fail("config must contain exactly version and tools")
	}
	versionRaw, ok := configRaw["version"]
	if !ok {
		fail("config must contain exactly version and tools")
	}
	var version string
	if err := json.Unmarshal(versionRaw, &version); err != nil || version == "" {
		fail("version must be a non-empty string")
	}
	toolsJSON, ok := configRaw["tools"]
	if !ok {
		fail("config must contain exactly version and tools")
	}
	var toolsRaw map[string]json.RawMessage
	if err := json.Unmarshal(toolsJSON, &toolsRaw); err != nil {
		fail("tools must be an object")
	}
	for name := range configRaw {
		if name != "version" && name != "tools" {
			fail("config must contain exactly version and tools")
		}
	}
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
	return version, tools
}

func loadMap(mapPath string) map[string]string {
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

	return mapping
}

func render(tools map[string]bool, mapping map[string]string, outPath string) {
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

func main() {
	if len(os.Args) == 3 && os.Args[1] == "version" {
		version, _ := loadConfig(os.Args[2])
		fmt.Println(version)
		return
	}
	if len(os.Args) == 5 && os.Args[1] == "render" {
		_, tools := loadConfig(os.Args[2])
		mapping := loadMap(os.Args[3])
		render(tools, mapping, os.Args[4])
		return
	}
	fail("usage: configure version <config.json>\n       configure render <config.json> <tool-map.json> <out-config.json>")
}
