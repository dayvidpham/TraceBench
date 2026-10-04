// Command config-value reads a value from the single parent harness config.
// Usage: config-value <config.json> version
package main

import (
	"encoding/json"
	"fmt"
	"os"
)

func main() {
	if len(os.Args) != 3 || os.Args[2] != "version" {
		fmt.Fprintln(os.Stderr, "usage: config-value <config.json> version")
		os.Exit(1)
	}
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		fmt.Fprintf(os.Stderr, "read config: %v\n", err)
		os.Exit(1)
	}
	var config struct {
		Version string `json:"version"`
	}
	if err := json.Unmarshal(data, &config); err != nil || config.Version == "" {
		fmt.Fprintln(os.Stderr, "config version must be a non-empty string")
		os.Exit(1)
	}
	fmt.Println(config.Version)
}
