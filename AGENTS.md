# Agent guide for TraceBench

TraceBench is a collection of Harbor benchmark tasks with network-restricted sandboxes. Some tasks
embed Go codebases, and task solutions, verifiers, or tooling may be Go. This file is the working
agreement for writing Go in this repository: layout, standards, tests, generated code, and landing
rules. `README.md` is authoritative for the task layout and the isolation model; `docs/architecture.md`
shows how the parts of the system connect (C4 diagrams and call sequences). The shared guide
in the parent directory (`../AGENTS.md`) owns worktree naming and the destructive-command
prohibition; this file adds the repository-local conventions.

When a repository-level Go module is added, its path is `github.com/dayvidpham/TraceBench`; Go code
inside a task may instead live in that task's own module.

## Commands

| Task | Command |
|---|---|
| Format check | `gofmt -l .` (must print nothing) |
| Vet | `go vet ./...` |
| Build | `go build ./...` |
| Test | `go test -race ./...` |
| Python tests | `uv run pytest` (uv workspace at the repository root) |
| Task pipeline | `uv run tracebench-corpus --corpus <dump> pipeline --prs <id-or-file> --repo-dir <clone> --index corpus/index/merged_prs.json --dest <dir> [--spec <spec>] [--run-id <id>]` |
| Tidy | `go mod tidy` (must leave `go.mod` and `go.sum` unchanged) |
| Commit | `git agent-commit -m "type(scope): summary"` (never plain `git commit`) |

## Gates

The full gate, in order:

1. `gofmt -l .` prints nothing.
2. `go vet ./...` passes.
3. `go build ./...` passes.
4. `go test -race ./...` passes.
5. `go mod tidy` leaves no diff.

Once a `Makefile` exists, `make check` is the single authoritative entry point and runs at least
the five steps above. Run the full gate before every commit that can affect it. Never weaken or
delete a test to make a gate pass; fix the cause.

For task changes, validate the task the way `README.md` documents, for example
`harbor run -p tasks/<name> -a oracle -e docker` (use `-e podman` on macOS Docker Desktop). Keep
the committed `task.toml` policy strict; relax it only on a scratch copy outside the repository.
Task tests are randomized and behavioral, so a hardcoded answer cannot pass; never add a golden
that a fixed output would satisfy.

The Go toolchain version is whatever `go.mod` declares. Do not restate it in prose.

## Layout

Tasks follow the Harbor layout in `README.md` (`tasks/<name>/` with `instruction.md`, `task.toml`,
`environment/`, `solution/`, and `tests/`). Do not move files out of that layout.

- Go code inside a task lives in the task's `environment/app/` snapshot (the code the agent works
  on), or beside its solution or verifier when it is part of the oracle.
- A repository-level Go module uses the standard layout: `cmd/<name>/` for binary entry points
  with thin `main` functions, `internal/` for packages not importable outside the module, `pkg/`
  only for packages deliberately published for external import, and `testdata/` for fixtures.
- Keep packages small; names are short, lower case, and free of stutter with their symbols.
- The Python loader lives in `loaders/tracebench_corpus/`, one module per step: `cli.py`
  (commands), `corpus.py` (dump loading and bundles), `task.py` (payload assembly), `golden.py`
  (golden suite and the canonical doublestar matcher), `test_manifest.py` (case catalog),
  `worktree.py` (secure worktree: packs the ancestry of `tree_commit` into a fresh repo), `oracle.py` (oracle patch and
  `solve.sh`), `repository_spec.py` (test and build command per repository), `target_config.py`
  (harness, model, thinking), `skeleton.py` (Harbor task directory), `verifier.py` (in-sandbox
  grading, standard library only), and `pipeline.py` (batch driver and Harbor job config). Its
  tests and `testdata/*.yaml` fixtures live in `loaders/tests/`.

### Task pipeline

`tracebench-corpus pipeline` builds one runnable Harbor task per pull request: payload, golden
suite, case catalog, secure worktree at `tree_commit` (real git history truncated at the pre-PR
commit; local identity; no remotes), verified
oracle, and the task with its verifier. It writes `<dest>/payloads/`, `<dest>/tasks/`, and
`<dest>/job-config.yaml` (run id, `n_attempts: 3`, `TRACEBENCH_RUN_ID`). A failed task names
the failed part and the others still build. Validate a built task with
`harbor run -p <dest>/tasks/<name> -a oracle -e podman` when Harbor is installed: generated tasks
score around 0.998 (the residual is the documented calibration set) and the hand-built
`tasks/peasant-344` MVP scores 1.0. The full runbook — dependencies, image setup with the `:keep`
re-tag step, and troubleshooting — is [`docs/proof-of-concept.md`](docs/proof-of-concept.md).

To modify it: the test and build command per repository live in the repository adaptation spec
(`--spec`, default in `repository_spec.py`); the reward rules and `test-results.json` live in
`verifier.py`; the task layout lives in `skeleton.py`; the test patterns and their matcher live
in `golden.py`. Every step fails closed. `loaders/README.md` ("Pipeline" and "How to modify")
is the reference; `docs/architecture.md` shows the flow.

## Go standards

- Standard library first. Add a dependency only with a stated reason, keep the set small, and
  run `go mod tidy` after any change.
- `gofmt` output is the only accepted formatting.
- Errors: return errors; do not panic for expected failures. Wrap with the operation and subject
  and `%w`: `fmt.Errorf("load trace %s: %w", id, err)`. Match with `errors.Is` and `errors.As`,
  never by string. Handle or propagate every error; `errcheck` applies.
- Context: `context.Context` is the first parameter on I/O, network, and other cancellable
  paths. Honor cancellation. Never store a context in a struct.
- Concurrency: every goroutine has an owner and a guaranteed exit. Prefer `sync.WaitGroup` or an
  error-collecting helper over ad-hoc channel coordination. Channels transfer ownership; mutexes
  protect shared state. Never use `time.Sleep` as synchronization. Race-enabled tests are the
  baseline.
- Naming: idiomatic Go; initialisms keep their case (`ID`, `URL`, `HTTP`). Doc comments on
  exported identifiers start with the identifier name.
- Types: make the zero value useful. Use a defined type over a bare string for closed sets.
  Pointers only for genuine optionality.
- Interfaces: small, and defined where they are consumed. Accept interfaces, return structs.
- Logging: `log/slog` for structured logs. No `fmt.Println` in packages; binary output goes to
  stdout or stderr deliberately.

## Tests

- Every behavior change adds or updates a test that fails without the change.
- `go test -race ./...` is the baseline. Focused packages still run with `-race`.
- Test cases live in `testdata/*.yaml` fixtures, never as inline case tables. The idiom is a
  typed struct, `//go:embed`, `yaml.Unmarshal`, a `Load...Fixtures()` helper, and pure
  non-vacuity validators.
- Deletion protection uses required-NAME manifests, never bare counts. Closed and security sets
  assert exact membership; a bare count is acceptable only when the count itself is the contract,
  stated in the fixture.
- Test observable behavior: return values, output, errors. Not internal state.
- Test the production path; do not mock the subject under test.
- A combinatorial change updates one fixture corpus, not several inline tables.

## Test promotion

Promote test complexity only when a lower-level test cannot observe the risk. Before adding an
elaborate harness (subprocess, database, screenshot, build matrix), record: the invariant it
protects; why a unit or fixture test cannot catch the failure; the production path it executes;
its maximum runtime and failure surface; the temporary files, directories, and processes it
creates and what happens after timeout or panic; concurrency isolation; whether the exact command
runs from a clean checkout in CI; the evidence it observes; a small mutation that makes it fail
for the intended reason; and the exit condition for simplifying or deleting it.

## Generated code

Generated files carry a `Code generated ... DO NOT EDIT.` header. Never hand-edit or hand-merge
them: change the source or the generator, rerun it, and commit byte-identical output. A freshness
test or CI check fails when the committed output drifts from a fresh generation.

## Branches, commits, and landing

- `main` is the default and integration branch.
- Feature work happens in a worktree at `<repo-root>/worktree/<branch>`, never by switching
  branches in the main checkout. Branch name:
  `TraceBench-<issue>--<type>--<short-name>` (for example
  `TraceBench-12--feat--corpus-loader`). The parent guide owns the full convention.
- Land through a GitHub pull request against `main`. Focused changes merge as one squash commit.
- Conventional commit subjects: `type(scope): summary`, imperative, lower case, no trailing
  period. One logical change per commit; commit early and often.
- Never force-push a shared branch, never install git hooks, never push tags by hand.
- After a merge: sync the local `main` checkout (`git -C <repo-root> pull --ff-only`), then
  remove the merged worktree and delete its branch.

## Hygiene

- Describe work by substance. Never put internal task-tracking terms (task IDs, wave, slice,
  phase, or epic names) in shipped code, comments, docs, tests, or commit messages.
- Preserve user-facing behavior outside the change. Do not delete prior functionality because
  surrounding code moved.
- Fail closed at trust boundaries: unknown values, malformed input, and missing configuration
  produce errors, not guesses.
- Never hand-merge a generated file on conflict: merge the source, rerun the generator, commit
  byte-identical output.

## Documentation

- `README.md` — task layout and the isolation model (authoritative).
- `docs/architecture.md` — C4 diagrams, dynamic views, and call sequences of the corpus and task-generation system.
- `loaders/README.md` — loader API, task payload contract, skeleton layout, the pipeline, the
  verifier contract, and how to modify them.
- `snapshot/README.md` — snapshot usage and the peasant binary contract.
- `tasks/_base/README.md` — shared base image build order and the leak model.

## Skills

Workflow skills live in `.claude/skills/`:

| Skill | Use |
|---|---|
| `issue-create-and-handle` | File one approved issue and hand it to the handler. |
| `issue-handler` | Carry one issue through worktree, implementation, review, CI, and merge. |
| `issue-orchestrator` | Run several approved issues as dependency-aware lanes. |
| `epic-composer` | Turn follow-up items into approved epics and sub-issues. |
| `review-wave` | Run the three-reviewer wave and publish one curated report. |
| `reviewer` | Independently review a PR or a wave and return ranked findings. |
| `c4-model` | Draw software architecture in ASCII C4. |
