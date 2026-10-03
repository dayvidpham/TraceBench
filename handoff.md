# Handoff

Issue: dayvidpham/TraceBench#4, Run OpenCode as the harness.
Branch: 4/run-opencode-as-the-harness
Repo tip this work is based on: f46761a (latest main at handoff).

## What is in this branch

`harness/opencode/` holds the copy trial, not a second checkout of OpenCode.

- `version` is `v1.18.34`.
- `pull.sh` fetches that ref with depth 1, checks the repo has one commit, writes `REVISION`, then deletes `.git`.
- `REVISION` is `aec0b9a6d8898f68f923aaf08b7306d931fd9d76`.
- `Containerfile` rebuilds by `FROM` an existing image and `COPY`. It does not `podman commit`.
- `prove.sh` and `TRIAL.md` are the trial that already ran.

`bin/opencode` and `src/` are not in git. The linux binary is about 185 MB, over GitHub's file limit. `src/` is the generated shallow tree. Both come back from `pull.sh` plus the linux asset for the same tag.

## What is running

On this computer, not in the branch:

`tb-opencode-trial` is up. Its image is `localhost/tracebench-peasant-smoke:latest`, built from `tasks/peasant-smoke` on `f46761a`. That is the newest container in the repo. OpenCode inside it prints `1.18.34`. The peasant tree inside the image is the Dockerfile pin `4153b00`.

The network on that task is public. `task.toml` says so. This branch does not change that file and does not point the trial at `tasks/trace-propagation`.

## What the first Grok run was not

The Grok CLI session "OpenCode Harness Podman Copy Trial" ran before this checkout was updated. It built `tracebench-trace-propagation` and did not call Harbor. Its proof script then stopped the container. That image is not the container this handoff uses.
