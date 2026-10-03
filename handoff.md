# Handoff

Issue: dayvidpham/TraceBench#4, Run OpenCode as the harness.
Branch: 4/run-opencode-as-the-harness
Repo tip this work is based on: f46761a (latest main at handoff).

## What is in this branch

`harness/opencode/` is a Harbor agent, not a container image and not a second checkout of OpenCode.

- `version` is `v1.18.34`.
- `pull.sh` fetches that ref with depth 1, checks the repo has one commit, writes `REVISION`, deletes `.git`, then downloads the linux release binary for this machine.
- `REVISION` is `aec0b9a6d8898f68f923aaf08b7306d931fd9d76`.
- `harness/deny` is the interface. The names are `search` and `fetch`.
- `harness/opencode/map` maps those to OpenCode: `websearch` and `webfetch`.
- `render-config.sh` writes `opencode.json` with those two permissions set to `deny`. It does not deny `bash`.
- `agent.py` class `OpenCode` is the Harbor agent. `install()` copies the payload in. `run()` starts OpenCode with the agent network locked and does not call a model.
- `prove.sh` runs `harbor run -p tasks/peasant-smoke -a harness.opencode.agent:OpenCode -e podman`.

`bin/opencode` and `src/` are not in git. The linux binary is over GitHub's file limit. `src/` is the generated shallow tree. Both come back from `pull.sh`.

## Which image

Harbor builds `tasks/peasant-smoke/environment/Dockerfile`. There is no image under `harness/`. The trial is not `tasks/trace-propagation`.

`tasks/peasant-smoke/task.toml` uses the trace-propagation stages. `[environment]` is `public` so setup can copy the harness. `[agent]` and `[verifier]` are `no-network`. The Dockerfile does not install a firewall. It bakes Go modules and sets `GOPROXY=off`. The peasant tree pin is `4153b00`.

## What the first Grok run was not

The Grok CLI session "OpenCode Harness Podman Copy Trial" ran before this checkout was updated. It built `tracebench-trace-propagation` and did not call Harbor. That image is not the container this handoff uses.

## Proven

`harness/opencode/prove.sh` exited 0 on 2026-10-03. Job `jobs/2026-10-03__16-33-10`, trial `peasant-smoke__Q2rgydh`, reward `1.0`. The stage note is `agent/opencode-stage.txt`:

```
version: 1.18.34
websearch: deny
webfetch: deny
bash: absent
webfetch-tool: disabled
websearch-tool: disabled
bash-tool: enabled
auto: explicit deny holds
connect: failed 4
```

`webfetch` and `websearch` are disabled on the `build` agent inside the container. The `bash` tool still runs. `opencode run --help` says `--auto` auto-approves permissions that are not explicitly denied. `connect: failed 4` is an HTTP/1.0 GET of `example.com:80` during the agent phase. The read got no status line. `run()` does not call a model.
