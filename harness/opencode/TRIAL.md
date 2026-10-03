# OpenCode Harbor stage

The container image is built by Harbor from `tasks/peasant-smoke/environment/Dockerfile`. `harness/opencode` does not build an image.

## Stages

`tasks/peasant-smoke/task.toml` follows `tasks/trace-propagation`:

- `[environment]` is `public`, so `agent.setup()` can copy the harness in.
- `[agent]` is `no-network` during `agent.run()`.
- `[verifier]` is `no-network`.

The Dockerfile does not add a firewall. It pins peasant at `4153b0026c9e73157ef663368a7bb497c022afc1`, bakes modules, and sets `GOPROXY=off`.

## Pin and config

`harness/opencode/version` is `v1.18.34`. `pull.sh` fetches that tag with depth 1, checks the clone has one commit, writes `REVISION`, deletes `.git`, and downloads the linux release binary for this machine.

`harness/deny` lists `search` and `fetch`. `harness/opencode/map` maps those to `websearch` and `webfetch`. `render-config.sh` writes `opencode.json` with those two permissions set to `deny`. `bash` is not in that object.

## Agent

`harness/opencode/agent.py` is the Harbor agent `OpenCode`.

`install()` uploads, while the environment network is still public:

- `src/` → `/opt/opencode`
- `bin/opencode` → `/usr/local/bin/opencode`
- `REVISION` → `/opt/opencode/REVISION`
- `opencode.json` → `/opt/opencode/opencode.json`

`run()` does not call a model. With the agent network locked it checks `opencode --version`, `OPENCODE_CONFIG=/opt/opencode/opencode.json opencode debug config`, that `webfetch` and `websearch` are disabled on the `build` agent, that the `bash` tool still runs, that `opencode run --help` keeps explicit denies in place under `--auto`, and an HTTP/1.0 GET of `example.com:80`. The egress proxy accepts the TCP handshake and then drops the payload, so the check requires an `HTTP/` status line. That read must fail. The note is `jobs/**/opencode-stage.txt`.

```bash
PYTHONPATH=. harbor run -p tasks/peasant-smoke \
  -a harness.opencode.agent:OpenCode -e podman
```

`harness/opencode/prove.sh` on 2026-10-03 wrote `jobs/2026-10-03__16-33-10/peasant-smoke__Q2rgydh/agent/opencode-stage.txt` and Harbor scored that trial `1.0`:

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
