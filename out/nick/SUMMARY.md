# Filtered oracle run: 14 target PRs

Run id: `tracebench-filtered-01a107ee-6459-7b21-906b-ee3beb41371e`
Environment: podman, oracle agent, 1 attempt per task (`-k 1`).
Pipeline built from the TEST-RUN worktree at `bb81610`.
Tasks regenerable from `/tmp/opencode/filtered-run` (3.1G payloads+tasks, not committed).

## Main run (2026-10-04, default 4-way parallel)

3 completed, 11 `HealthcheckError` (Go-proxy throttling in the env warm phase).

Completed:

- `peasant-pr-0316`: 0.9841
- `peasant-pr-0334`: 0.6255
- `peasant-pr-0347`: 0.9635

HealthcheckError (all re-run candidates, tasks themselves verified healthy):

- `peasant-pr-0145`, `peasant-pr-0146`, `peasant-pr-0150`, `peasant-pr-0157`
- `peasant-pr-0310`, `peasant-pr-0315`, `peasant-pr-0331`, `peasant-pr-0337`
- `peasant-pr-0338`, `peasant-pr-0343`, `peasant-pr-0344`

Mean over completed: 0.184 (11 errors count as 0).

## Solo re-run `peasant-pr-0145` (2026-10-04, serial)

Reward 0.9975, 0 exceptions (`runs/2026-10-04__11-36-46-solo-pr-0145/`).
Confirms the HealthcheckErrors were parallel-run throttling, not broken tasks.

## Retry of remaining 10 (`-n 2`, `inputs/job-config-retry1.yaml`)

Pending at packaging time; appended in a follow-up commit.

## Layout

- `inputs/`: `prs-filtered.txt` (14 targets), `job-config.yaml`, `job-config-retry1.yaml`,
  `merged_prs.json` (51-record index built via `gh api`, covers every prior-context candidate).
- `runs/<job>/result.json`: job-level results; `runs/<job>/trials/<trial>/` holds
  `result.json`, `trial.log`, `exception.txt` (failures), and
  `verifier/{reward.txt,test-results.json}` (completed trials).
- Excluded: `test-stdout.jsonl` streams (~8M each) and the 3.1G `payloads/`+`tasks/` build tree.
