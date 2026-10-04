#!/usr/bin/env bash
# Ensure the shared task images are tagged for a Harbor run.
#
# Harbor's compose teardown (`docker compose down --rmi local`) removes the
# image referenced by the task, so `tracebench/task-runtime:latest` can vanish
# after any run. The images carry a `:keep` alias that survives the teardown;
# this script re-tags `:latest` from it. If the alias is gone too, rebuild per
# tasks/_base/README.md.
set -euo pipefail

retag() {
  local keep="$1" tag="$2"
  if podman image exists "$keep"; then
    podman tag "$keep" "$tag"
    echo "tagged $tag from $keep"
  elif podman image exists "$tag"; then
    echo "$tag already present"
  else
    echo "missing $tag and $keep; rebuild per tasks/_base/README.md" >&2
    return 1
  fi
}

retag tracebench/peasant-base:keep tracebench/peasant-base:latest
retag tracebench/task-runtime:keep tracebench/task-runtime:latest
# The hand-built feasibility task pins the snapshot tag.
retag tracebench/peasant-base:keep tracebench/peasant-base:838a6dd0a73524db6ae96a931c224ff66090aa70
