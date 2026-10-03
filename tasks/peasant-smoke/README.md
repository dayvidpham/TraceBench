# tracebench/peasant-smoke

Containerization smoke task for TraceBench issue #1.

The image pins `peasant-labs/peasant` at `4153b0026c9e73157ef663368a7bb497c022afc1`
(full history kept for later snapshot/cutoff work), bakes the Go module cache
and build, and runs hermetic (`GOPROXY=off`). The verifier runs
`go build ./...` plus `go test -count=1 ./internal/defaults/...`.

All phases are intentionally `public` — network lockdown is issue #6.
