# tracebench/peasant-smoke

Containerization smoke task for TraceBench issue #1.

The image pins `peasant-labs/peasant` at `4153b0026c9e73157ef663368a7bb497c022afc1`
(full history kept for later snapshot/cutoff work), bakes the Go module cache
and build, and runs hermetic (`GOPROXY=off`). The verifier runs
`go build ./...` plus `go test -count=1 ./internal/defaults/...`.

The environment phase is `public` so agent setup can install the harness.
The agent and verifier phases are `no-network`, same stages as
`tasks/trace-propagation`. The image still bakes modules and sets
`GOPROXY=off`, so verify does not fetch.
